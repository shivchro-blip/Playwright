"""Missing logins come from the secret store when a run starts (a fake store command stands in for a vault)."""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from tests.e2e.test_ui import running_app

FAKE = Path(__file__).resolve().parent / "fixtures" / "vault" / "fake_secret.py"
SUITE = Path("Tests") / "SMOKE" / "MasterRegressionSuite"


def prepare(workspace: Path) -> None:
    data_file = workspace / "Data" / "systemData.json"
    data = json.loads(data_file.read_text(encoding="utf-8"))
    data["systems"]["SMOKE"]["dev"].update({"username_env": "VAULT_USER", "password_env": "VAULT_PASS"})
    data_file.write_text(json.dumps(data), encoding="utf-8")
    # The page has one field whose id is the secret itself: the locator finds it only if VAULT_PASS reached the run
    (workspace / "Pages" / "SMOKE" / "VaultPage.json").write_text(json.dumps({
        "path": "/vault.html", "fields": {"secret": {"locator": "#ENV.VAULT_PASS", "type": "text"}}}),
        encoding="utf-8")
    (workspace / SUITE / "Vault.json").write_text(json.dumps({"testCases": [{
        "testCaseID": "TC-VAULT-001", "description": "Finds the field the store's password names.", "system": "SMOKE",
        "module": "vault", "environment": ["dev"], "tags": ["vault"],
        "steps": [{"screen": "VaultPage", "formData": {"secret": "typed"}}]}]}), encoding="utf-8")


def start_and_wait(url: str) -> dict:
    request = urllib.request.Request(f"{url}api/runs", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"system": "SMOKE", "env": "dev", "tags": ["vault"],
                                                      "browsers": ["chromium"],
                                                      "options": {"headlessMode": True}}).encode())
    run_id = json.loads(urllib.request.urlopen(request, timeout=30).read())["id"]
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        run = json.loads(urllib.request.urlopen(f"{url}api/runs/{run_id}", timeout=30).read())
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not finish")


def test_a_run_gets_its_password_from_the_store_and_the_log_hides_it(workspace: Path, tmp_path: Path):
    prepare(workspace)
    store = {"SECRET_PROVIDER": "command", "SECRET_COMMAND": json.dumps([sys.executable, str(FAKE), "{name}"])}
    with running_app(tmp_path, **store) as url:
        run = start_and_wait(url)
    log = (tmp_path / "runs" / run["id"] / "console.log").read_text(encoding="utf-8")
    assert run["status"] == "passed", log[-2000:]
    assert "s3cr3t-from-vault" not in log and "s3cr3t-from-vault" not in json.dumps(run)


def test_without_a_store_the_same_run_cannot_find_the_password(workspace: Path, tmp_path: Path):
    prepare(workspace)
    with running_app(tmp_path) as url:
        run = start_and_wait(url)
    assert run["status"] == "failed"
