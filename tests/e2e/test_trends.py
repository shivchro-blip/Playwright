"""The Trends dialog on a History made of real run folders."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def write_run(runs: Path, rid: str, status: str, results: list[tuple[str, str, float]]) -> None:
    folder = runs / rid
    folder.mkdir(parents=True)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    (folder / "run.json").write_text(json.dumps({
        "id": rid, "config": {"system": "SMOKE", "env": "dev", "tags": ["smoke"], "browsers": ["chromium"],
                              "workers": 1, "options": {}, "test_ids": [], "runner": ""},
        "status": status, "created_at": now, "started_at": now, "finished_at": now, "exit_code": 0 if
        status == "passed" else 1, "results": [
            {"nodeid": f"n{i}", "test_id": t, "browser": "chromium", "outcome": o, "duration": d, "message": "",
             "retries": 0} for i, (t, o, d) in enumerate(results)]}), encoding="utf-8")


def test_trends_dialog_shows_days_slowest_tests_and_failures(workspace: Path, tmp_path: Path):
    runs = tmp_path / "runs"
    write_run(runs, "20261101-100000-aaaa", "passed", [("TC-FAST", "passed", 1.0), ("TC-SLOW", "passed", 30.0)])
    write_run(runs, "20261101-110000-bbbb", "failed", [("TC-FAST", "failed", 2.0), ("TC-SLOW", "passed", 40.0)])
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="History").click()
        page.get_by_test_id("open-trends").click()
        dialog = page.get_by_role("dialog", name="Trends")
        days = dialog.get_by_test_id("trend-days")
        expect(days).to_contain_text("75.0% of 4 test results passed, 2 runs")
        expect(days.locator(".any-bar")).to_have_count(30)
        slowest = dialog.get_by_test_id("trend-slowest")
        expect(slowest.locator(".any-hbar-row").first).to_contain_text("TC-SLOW")
        expect(slowest).to_contain_text("35 s avg, 40 s max")
        expect(dialog.get_by_test_id("trend-spots")).to_contain_text("(no step detail)")  # no Allure results here
        dialog.get_by_label("Period").click()
        page.get_by_role("option", name="Last 7 days").click()
        expect(days.locator(".any-bar")).to_have_count(7)
        browser.close()
