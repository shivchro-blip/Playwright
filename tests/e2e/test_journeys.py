"""Journeys tab and API: create a journey from test cases, see its status from real runs, run it."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app


def test_create_a_journey_see_its_status_and_coverage_and_run_it(workspace: Path, tmp_path: Path):
    passed = run_and_wait(workspace, tmp_path, tags=["smoke"])
    assert passed.status == "passed"
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Journeys").click()
        expect(page.get_by_test_id("journeys-empty")).to_be_visible()
        expect(page.get_by_test_id("journeys-unassigned")).to_contain_text("TC-SMOKE-001-Search")

        page.get_by_test_id("journey-new").click()
        page.get_by_test_id("jr-name").fill("Search the site")
        page.get_by_test_id("jr-crit").click()
        page.get_by_role("option", name="critical").click()
        page.get_by_test_id("jr-owner").fill("Web team")
        tests = page.get_by_test_id("jr-tests")
        tests.click()
        page.get_by_role("option", name="TC-SMOKE-001-Search").click()
        page.get_by_role("option", name="TC-SMOKE-002-NotesAutoGen").click()
        page.keyboard.press("Escape")
        page.get_by_test_id("jr-save").click()
        expect(page.get_by_text("Saved Search the site")).to_be_visible()

        row = page.get_by_test_id("journey-row-search-the-site")
        expect(row).to_contain_text("critical")
        expect(row).to_contain_text("Web team")
        expect(page.get_by_test_id("journey-state-search-the-site-dev")).to_contain_text("passing")
        expect(row).to_contain_text("100% of last 1")
        expect(row).to_contain_text("% of fields")
        expect(page.get_by_test_id("journeys-summary")).to_contain_text("dev: 1 of 1 passing")

        row.locator("td").first.click()
        detail = page.get_by_test_id("journey-detail")
        expect(detail).to_contain_text("TC-SMOKE-002-NotesAutoGen")
        expect(detail.get_by_test_id("journey-coverage")).to_contain_text("SmokeHome")
        expect(detail.get_by_test_id("journey-coverage")).to_contain_text("of 5 fields")
        detail.get_by_role("button", name="Close").click()

        page.get_by_test_id("journey-run-search-the-site").click()
        page.locator(".q-menu").get_by_text("on dev").click()
        expect(page.get_by_text("Running the journey on dev")).to_be_visible()
        expect(page.get_by_role("tab", name="Control")).to_have_attribute("aria-selected", "true")
        browser.close()
    saved = json.loads((workspace / "Data" / "journeys.json").read_text(encoding="utf-8"))["journeys"][0]
    assert saved == {"id": "search-the-site", "name": "Search the site", "system": "SMOKE",
                     "criticality": "critical", "owner": "Web team", "description": "",
                     "tests": ["TC-SMOKE-001-Search", "TC-SMOKE-002-NotesAutoGen"]}
    started = [json.loads(p.read_text())["config"] for p in (tmp_path / "runs").glob("*/run.json")]
    journey_run = next(c for c in started if c["test_ids"])
    assert journey_run["test_ids"] == ["TC-SMOKE-001-Search", "TC-SMOKE-002-NotesAutoGen"]
    assert journey_run["tags"] and journey_run["env"] == "dev"


def test_api_lists_status_and_runs_a_journey(workspace: Path, tmp_path: Path):
    (workspace / "Data" / "journeys.json").write_text(json.dumps({"journeys": [
        {"id": "broken", "name": "Broken flow", "system": "SMOKE", "criticality": "high",
         "tests": ["TC-SMOKE-004-MissingElement"]},
        {"id": "empty", "name": "Nothing yet", "system": "SMOKE", "tests": []}]}), encoding="utf-8")
    run_and_wait(workspace, tmp_path, tags=["missing"])
    with running_app(tmp_path) as url:
        journeys = json.loads(urllib.request.urlopen(f"{url}api/journeys?system=SMOKE", timeout=60).read())
        broken = next(j for j in journeys if j["id"] == "broken")
        assert broken["worst"] == "failing" and broken["statuses"]["dev"]["state"] == "failing"
        assert broken["statuses"]["dev"]["where"] == "SmokeHome > missing (FILL)"
        assert broken["coverage"]["screens"][0]["screen"] == "SmokeHome"
        assert next(j for j in journeys if j["id"] == "empty")["worst"] == "not_run"

        def post(path):
            req = urllib.request.Request(f"{url}{path}", method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()

        assert post("api/journeys/empty/run?env=dev")[0] == 400  # no test cases to run
        assert post("api/journeys/broken/run?env=nowhere")[0] == 400
        status, run = post("api/journeys/broken/run?env=dev")
        assert status == 201 and run["config"]["test_ids"] == ["TC-SMOKE-004-MissingElement"]
        assert run["trigger"] == "journey:broken"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = json.loads(urllib.request.urlopen(f"{url}api/runs/{run['id']}", timeout=30).read())
            if current["status"] not in ("queued", "running"):
                break
            time.sleep(0.5)
