"""Secret store: the commands it builds, what it returns, and what it refuses."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from webapp.services.vault import Vault, missing_credentials

FAKE = Path(__file__).resolve().parents[1] / "e2e" / "fixtures" / "vault" / "fake_secret.py"


def lookup(**values):
    return lambda key: values.get(key)


def command_vault(**extra) -> Vault:
    return Vault(lookup(SECRET_PROVIDER="command", SECRET_COMMAND=json.dumps([sys.executable, str(FAKE), "{name}"]),
                        **extra))


def test_nothing_is_configured_by_default():
    vault = Vault(lookup())
    assert not vault.configured() and vault.blocker() == "" and vault.fetch(["A"]).values == {}


@pytest.mark.parametrize("values, expected", [
    ({"SECRET_PROVIDER": "keepass"}, "must be one of"),
    ({"SECRET_PROVIDER": "azure-keyvault"}, "SECRET_AZURE_VAULT"),
    ({"SECRET_PROVIDER": "command"}, "JSON list"),
    ({"SECRET_PROVIDER": "command", "SECRET_COMMAND": '{"a": 1}'}, "JSON list of strings"),
    ({"SECRET_PROVIDER": "command", "SECRET_COMMAND": '["prog", "x"]'}, "{name}"),
])
def test_setup_problems_are_named(values, expected):
    assert expected in Vault(lookup(**values)).blocker()


def test_azure_keyvault_command_uses_dashes_and_the_prefix(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda exe: f"/usr/bin/{exe}")
    vault = Vault(lookup(SECRET_PROVIDER="azure-keyvault", SECRET_AZURE_VAULT="kv-qa", SECRET_NAME_PREFIX="qa-"))
    assert vault.blocker() == ""
    assert vault.argv("NYBE_PASSWORD") == [
        "/usr/bin/az", "keyvault", "secret", "show", "--vault-name", "kv-qa", "--name", "qa-NYBE-PASSWORD",
        "--query", "value", "-o", "tsv"]


def test_only_plain_names_are_passed_to_the_command():
    vault = command_vault()
    for bad in ("a b", "x;rm -rf /", "$(id)", "", "1abc", "a" * 65):
        with pytest.raises(ValueError):
            vault.argv(bad)
    assert vault.argv("OK_NAME")[-1] == "OK_NAME"


def test_values_come_back_without_the_final_newline_and_misses_say_why():
    got = command_vault().fetch(["VAULT_USER", "VAULT_PASS", "MISSING_ONE"])
    assert got.values == {"VAULT_USER": "vault-user", "VAULT_PASS": "s3cr3t-from-vault"}
    assert got.errors == {"MISSING_ONE": "the store said: secret MISSING_ONE not found in the store"}


def test_a_failing_or_slow_or_absent_program_is_reported_without_a_secret(monkeypatch):
    def slow(*a, **kw):
        raise subprocess.TimeoutExpired("x", 1)

    assert "did not answer" in Vault(lookup(SECRET_PROVIDER="command", SECRET_COMMAND='["x","{name}"]'),
                                     run=slow).fetch(["A"]).errors["A"]
    gone = Vault(lookup(SECRET_PROVIDER="command", SECRET_COMMAND='["/no/such/program","{name}"]'))
    assert "Could not run the secret command" in gone.fetch(["A"]).errors["A"]
    empty = Vault(lookup(SECRET_PROVIDER="command", SECRET_COMMAND=json.dumps(
        [sys.executable, "-c", "print()", "{name}"])))
    assert empty.fetch(["A"]).errors == {"A": "not found"}
    blocked = Vault(lookup(SECRET_PROVIDER="command", SECRET_COMMAND="nope"))
    assert "JSON list" in blocked.fetch(["A"]).errors["A"]


def test_only_missing_unsupplied_names_are_asked_for():
    status = [{"key": "U", "set": True}, {"key": "P", "set": False}, {"key": "Q", "set": False}]
    assert missing_credentials(status, {"Q": "typed"}) == ["P"]
    assert missing_credentials(None, {}) == [] and missing_credentials([], {}) == []


def test_prepare_credentials_merges_typed_values_then_the_store(monkeypatch):
    from types import SimpleNamespace

    from webapp.state import Services

    status = {"dev": [{"key": "VAULT_USER", "set": False}, {"key": "VAULT_PASS", "set": False}],
              "test": [{"key": "VAULT_PASS", "set": False}, {"key": "OTHER", "set": True}]}
    me = SimpleNamespace(credential_status=lambda system, env: status.get(env), vault=command_vault())
    prepare = lambda *a, **kw: Services.prepare_credentials(me, *a, **kw)  # noqa: E731

    values, missing, errors = prepare("S", ["dev", "test"], {"VAULT_USER": "typed-user"})
    assert values == {"VAULT_USER": "typed-user", "VAULT_PASS": "s3cr3t-from-vault"}  # typed wins, store fills in
    assert missing == [] and errors == {}

    values, missing, _ = prepare("S", ["dev"], {}, use_vault=False)  # a remote runner uses its own store
    assert values == {} and missing == ["VAULT_USER", "VAULT_PASS"]

    me.vault = Vault(lookup())  # no store set up: only what was typed
    assert prepare("S", ["dev"], {"VAULT_PASS": "x"}) == ({"VAULT_PASS": "x"}, ["VAULT_USER"], {})

    me.vault = command_vault()
    status["dev"] = [{"key": "NOT_IN_STORE", "set": False}]
    values, missing, errors = prepare("S", ["dev"])
    assert values == {} and missing == ["NOT_IN_STORE"] and "not found in the store" in errors["NOT_IN_STORE"]
    assert prepare("S", [None]) == ({}, [], {})  # an environment with no login entry needs nothing


def test_a_schedule_runs_with_the_stores_values_unless_it_targets_a_runner():
    from types import SimpleNamespace

    from webapp.services.schedules import Scheduler, validate

    started = []
    runs = SimpleNamespace(listeners=[], start=lambda config, **kw: started.append((config.runner, kw)) or
                           SimpleNamespace(id="r1"))
    catalog = SimpleNamespace(load=lambda system: SimpleNamespace(select=lambda tags: []))
    scheduler = Scheduler(SimpleNamespace(), runs, catalog, None)
    calls = []
    scheduler.credentials_for = lambda system, env, vault: calls.append(vault) or {"PW": "from-store"}
    base = {"name": "n", "cron": "0 6 * * *", "system": "S", "env": "dev", "tags": ["smoke"]}
    scheduler._start(validate(base))
    scheduler._start(validate({**base, "runner": "lab1"}))
    assert [k.get("credentials") for _, k in started] == [{"PW": "from-store"}, {"PW": "from-store"}]
    assert calls == [True, False]  # the second one is for a remote runner, which has its own store

    def broken(*a):
        raise RuntimeError("store is down")

    scheduler.credentials_for = broken
    scheduler._start(validate(base))
    assert "credentials" not in started[-1][1]  # still starts; the login steps will say what is missing


def test_the_runner_agent_fetches_what_its_own_machine_lacks(tmp_path, monkeypatch):
    from webapp.runner_agent import fetch_missing_credentials
    from webapp.services.run_manager import Run, RunConfig

    workspace = tmp_path / "ws"
    (workspace / "Data").mkdir(parents=True)
    (workspace / "Data" / "systemData.json").write_text(json.dumps({"systems": {"S": {"dev": {
        "base_url": "http://x", "username_env": "VAULT_USER", "password_env": "VAULT_PASS"}}}}), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(f"SECRET_PROVIDER=command\nSECRET_COMMAND={json.dumps([sys.executable, str(FAKE), '{name}'])}\n"
                        "VAULT_USER=already-here\n", encoding="utf-8")
    monkeypatch.delenv("VAULT_USER", raising=False)
    monkeypatch.delenv("VAULT_PASS", raising=False)
    run = Run(id="r", config=RunConfig(system="S", env="dev", tags=["t"]), dir=tmp_path)
    fetch_missing_credentials(run, workspace, env_file)
    assert run._secrets == {"VAULT_PASS": "s3cr3t-from-vault"}  # the user name is in its .env, so it is not fetched
    quiet = Run(id="r2", config=run.config, dir=tmp_path)
    fetch_missing_credentials(quiet, workspace, None)  # no store set up on a machine without a .env
    assert quiet._secrets == {}
