"""Run what matters end to end: Control > Plan picks the test cases for a budget, says why, and runs them."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def test_plan_a_run_in_a_budget_and_run_it(workspace: Path, tmp_path: Path):
    (workspace / "Data" / "journeys.json").write_text(json.dumps({"journeys": [
        {"id": "smoke", "name": "Smoke search", "system": "SMOKE", "criticality": "critical",
         "tests": ["TC-SMOKE-001-Search"]}]}), encoding="utf-8")
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        api = json.loads(urllib.request.urlopen(urllib.request.Request(
            url + "api/v1/plans", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"system": "SMOKE", "env": "dev", "budget_minutes": 5}).encode()), timeout=20).read())
        first = api["chosen"][0]
        assert first["test_id"] == "TC-SMOKE-001-Search" and first["must"], api
        assert "critical journey: Smoke search" in first["reasons"]
        assert all("never run on dev" in c["reasons"] for c in api["chosen"][1:]), api

        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.get_by_test_id("plan-open").click()
        dialog = page.get_by_test_id("plan-dialog")
        dialog.get_by_test_id("plan-make").click()
        expect(dialog.get_by_test_id("plan-summary")).to_contain_text(f"{len(api['chosen'])} test case")
        expect(dialog.get_by_test_id("plan-chosen")).to_contain_text("critical journey: Smoke search")
        dialog.get_by_test_id("plan-run").click()
        expect(page.get_by_text("started with")).to_be_visible()
        browser.close()
    assert problems == []
