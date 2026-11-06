"""Run the same tests on two environments and compare them: paired runs, the Also run on menu, the comparison."""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app
from webapp.services.run_insights import partner_run
from webapp.services.run_manager import RunConfig, RunManager, TestResult


def finished_pair(workspace: Path, tmp_path: Path, monkeypatch) -> list:
    """Two paired runs (dev, test) that "ran": TC-A passes on dev and fails on test."""
    outcomes = {"dev": {"TC-A": "passed", "TC-B": "passed"}, "test": {"TC-A": "failed", "TC-B": "passed"}}

    def fake_execute(self, run):
        run.status, run.exit_code = "failed" if "failed" in outcomes[run.config.env].values() else "passed", 0
        for test_id, outcome in outcomes[run.config.env].items():
            nodeid = f"suite::t[chromium-{test_id}]"
            run.results[nodeid] = TestResult(nodeid, test_id, "chromium", outcome, 1.0)
        run.report_status = "unavailable"
        run.finished_at = run.started_at = run.created_at
        self._save(run)
        self._dispatch()

    monkeypatch.setattr(RunManager, "_execute", fake_execute)
    manager = RunManager(repo_root=Path(__file__).resolve().parents[2], workspace=workspace,
                         runs_dir=tmp_path / "runs", python_exe=sys.executable)
    runs = manager.start_compared(RunConfig(system="SMOKE", env="dev", tags=["smoke"], browsers=["chromium"]),
                                  ["test", "dev", "test"])
    deadline = time.monotonic() + 20
    while any(r.active for r in runs) and time.monotonic() < deadline:
        time.sleep(0.05)
    # The run threads save run.json after setting the status: wait for them, or a read can meet a file being
    # replaced (Windows refuses to open it then).
    for thread in threading.enumerate():
        if thread.name in {f"run-{r.id}" for r in runs}:
            thread.join(timeout=20)
    return runs


def test_runs_are_paired_and_saved(workspace, tmp_path, monkeypatch):
    dev, test = finished_pair(workspace, tmp_path, monkeypatch)
    assert (dev.config.env, test.config.env) == ("dev", "test")  # the repeated and own environments are skipped
    assert dev.compare_with == test.id and test.compare_with == dev.id
    assert test.config.tags == dev.config.tags and test.config.options is not dev.config.options
    runs = [dev, test]
    assert partner_run(runs, dev) is test and partner_run(runs, test) is dev
    saved = json.loads((test.dir / "run.json").read_text(encoding="utf-8"))
    assert saved["compare_with"] == dev.id and saved["config"]["env"] == "test"
    again = RunManager(repo_root=Path("."), workspace=workspace, runs_dir=tmp_path / "runs", python_exe=sys.executable)
    assert again.get(test.id).compare_with == dev.id


def test_a_running_partner_is_not_offered(workspace, tmp_path, monkeypatch):
    dev, test = finished_pair(workspace, tmp_path, monkeypatch)
    test.status = "running"
    assert partner_run([dev, test], dev) is None


def test_the_comparison_opens_with_the_other_environment(workspace, tmp_path, monkeypatch):
    dev, test = finished_pair(workspace, tmp_path, monkeypatch)
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="History").click()
        page.locator("tbody tr", has_text="SMOKE/test").first.click()
        page.get_by_role("button", name="Compare").click()
        dialog = page.get_by_role("dialog", name="Compare runs")
        expect(dialog.get_by_test_id("cmp-env-note")).to_contain_text("dev → test")
        expect(dialog.get_by_test_id("cmp-summary")).to_contain_text("1 new failure")
        expect(dialog.locator("th", has_text="dev").first).to_be_visible()
        expect(dialog.locator("tbody")).to_contain_text("TC-A")
        browser.close()


def test_the_api_starts_paired_runs_and_checks_the_environments(workspace, tmp_path):
    with running_app(tmp_path) as url:
        def post(body):
            request = urllib.request.Request(f"{url}api/runs", method="POST", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()

        status, body = post({"system": "SMOKE", "env": "dev", "tags": ["nothing-matches"], "browsers": ["chromium"],
                             "also_envs": ["nowhere"]})
        assert status == 400 and "Unknown environment" in body
