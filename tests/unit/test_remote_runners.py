"""Remote runners: the token store, the zip exchange and RunManager's claim/events/finish path (no real pytest)."""
from __future__ import annotations

import json
import sys
import time
import zipfile

import pytest

from webapp.pytest_events import PREFIX
from webapp.services import run_manager
from webapp.services.archives import ArchiveError, extract_safely, zip_workspace
from webapp.services.run_manager import RunConfig, RunManager
from webapp.services.runners import PROTOCOL, RunnerStore


@pytest.fixture
def store(tmp_path):
    return RunnerStore(tmp_path / "reports" / "runners.json")


@pytest.fixture
def manager(tmp_path, store):
    store.add("lab1")
    store.add("lab2")
    return RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs",
                      python_exe=sys.executable, runners=store)


def cfg(**kw) -> RunConfig:
    return RunConfig(**{"system": "SMOKE", "env": "dev", "tags": ["smoke"], "browsers": ["chromium"], **kw})


def event(**data) -> str:
    return PREFIX + json.dumps(data)


def test_token_is_shown_once_and_only_its_hash_is_stored(store):
    token = store.add("lab1")
    assert token.startswith("apr_") and len(token) > 30
    assert token not in store.path.read_text(encoding="utf-8")
    assert store.by_token(token) == "lab1"
    assert store.by_token(token + "x") is None and store.by_token("apr_nope") is None and store.by_token("") is None
    assert store.names() == ["lab1"]


def test_names_are_checked_and_unique_and_removal_revokes(store):
    token = store.add("lab1")
    for bad in ("", "a b", "../x", "x" * 41, "-lead"):
        with pytest.raises(ValueError):
            store.add(bad)
    with pytest.raises(ValueError, match="already exists"):
        store.add("lab1")
    assert store.remove("lab1") and not store.remove("lab1")
    assert store.by_token(token) is None


def test_online_means_called_in_recently(store):
    store.add("lab1")
    assert not store.online("lab1")
    store.touch("lab1")
    assert store.online("lab1") and store.list()[0]["online"] is True


def test_workspace_zip_has_definitions_only(tmp_path):
    ws = tmp_path / "ws"
    for rel in ("Data/systemData.json", "Pages/S/A.json", "Tests/S/T.json", ".env", "reports/users.json",
                "reports/runs/r1/console.log"):
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text("x", encoding="utf-8")
    archive = zip_workspace(ws)
    try:
        names = sorted(zipfile.ZipFile(archive).namelist())
    finally:
        archive.unlink()
    assert names == ["Data/systemData.json", "Pages/S/A.json", "Tests/S/T.json"]


def test_unpacking_refuses_escapes_links_and_oversize(tmp_path):
    def build(name, entries, link=False):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as z:
            for entry, payload in entries.items():
                info = zipfile.ZipInfo(entry)
                if link:
                    info.external_attr = 0o120777 << 16
                z.writestr(info, payload)
        return path

    ok = build("ok.zip", {"a/b.txt": "hi"})
    assert extract_safely(ok, tmp_path / "out") == 1 and (tmp_path / "out" / "a" / "b.txt").read_text() == "hi"
    with pytest.raises(ArchiveError, match="leaves"):
        extract_safely(build("up.zip", {"../evil.txt": "x"}), tmp_path / "out2")
    assert not (tmp_path / "evil.txt").exists()
    with pytest.raises(ArchiveError, match="Links"):
        extract_safely(build("link.zip", {"l": "/etc/passwd"}, link=True), tmp_path / "out3")
    with pytest.raises(ArchiveError, match="too large"):
        extract_safely(build("big.zip", {"f": "x" * 100}), tmp_path / "out4", max_bytes=10)
    with pytest.raises(ArchiveError, match="Not a zip"):
        (tmp_path / "t.zip").write_text("nope")
        extract_safely(tmp_path / "t.zip", tmp_path / "out5")


def test_unknown_runner_and_local_only_options_are_refused(manager):
    with pytest.raises(ValueError, match="Unknown runner"):
        manager.start(cfg(runner="nobody"))
    with pytest.raises(ValueError, match="only works on this machine"):
        manager.start(cfg(runner="lab1", options={"saveCheckpoints": True}))


def test_a_remote_run_waits_for_its_own_runner_one_at_a_time(manager):
    first, second = manager.start(cfg(runner="lab1")), manager.start(cfg(runner="lab1"))
    other = manager.start(cfg(runner="lab2"))
    assert [r.status for r in (first, second, other)] == ["queued"] * 3  # nothing started locally
    assert manager.queue_position(second.id) == 2 and manager.queue_position(other.id) == 1
    assert manager.claim("nobody") is None
    claimed = manager.claim("lab1")
    assert claimed is first and first.status == "running" and "Started on runner lab1" in first.log[-1]
    assert manager.claim("lab1") is None  # busy with the first
    assert manager.claim("lab2") is other


def test_remote_runs_do_not_block_local_ones(manager, monkeypatch):
    ran = []
    monkeypatch.setattr(RunManager, "_execute",
                        lambda self, run: ran.append(run.id) or setattr(run, "status", "passed"))
    remote = manager.start(cfg(runner="lab1"))
    local = manager.start(cfg())
    deadline = time.monotonic() + 5
    while not ran and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ran == [local.id] and remote.status == "queued"


def test_output_events_results_and_finish(manager):
    run = manager.start(cfg(runner="lab1"))
    run._secrets = {"PW": "hunter22"}
    manager.claim("lab1")
    assert manager.remote_run(run.id, "lab2") is None and manager.remote_run("nope", "lab1") is None
    node = "tests/dynamic_test_suite.py::test_dynamic_execution[chromium-TC-1]"
    cancel = manager.remote_events(run, [event(event="collected", count=1), event(event="start", nodeid=node),
                                         "typed hunter22 into the form",
                                         event(event="test", nodeid=node, outcome="passed", when="call", duration=1.5)])
    assert cancel is False and run.collected == 1
    assert [(r.test_id, r.outcome) for r in run.results.values()] == [("TC-1", "passed")]
    assert "typed *** into the form" in run.log and "hunter22" not in (run.dir / "console.log").read_text()
    assert manager.remote_finish(run, 0)
    assert run.status == "passed" and run.exit_code == 0 and run.finished_at
    assert manager.remote_run(run.id, "lab1") is None and not manager.remote_finish(run, 1)


def test_cancel_reaches_the_runner_with_the_next_call(manager):
    run = manager.start(cfg(runner="lab1"))
    manager.claim("lab1")
    assert manager.cancel(run.id)
    assert manager.remote_events(run, []) is True
    manager.remote_finish(run, -15)
    assert run.status == "cancelled"


def test_cancel_while_queued_for_a_runner(manager):
    run = manager.start(cfg(runner="lab1"))
    assert manager.cancel(run.id) and run.status == "cancelled" and manager.claim("lab1") is None


def test_a_silent_runner_marks_the_run_lost(manager, monkeypatch):
    monkeypatch.setattr(run_manager, "WATCH_INTERVAL", 0.05)
    monkeypatch.setattr(run_manager, "RUNNER_SILENCE_SECONDS", 0.1)
    run = manager.start(cfg(runner="lab1"))
    manager.claim("lab1")
    deadline = time.monotonic() + 5
    while run.active and time.monotonic() < deadline:
        time.sleep(0.05)
    assert run.status == "error" and "Runner lab1 stopped responding" in run.error


def test_protocol_is_a_positive_int():
    assert isinstance(PROTOCOL, int) and PROTOCOL >= 1
