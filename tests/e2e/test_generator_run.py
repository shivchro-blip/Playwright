"""Generator > Run runs only the open test case and shows its run on the same page."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def test_run_stays_on_the_page_and_runs_only_this_test_case(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text("TC-SMOKE-002").first.click()
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-002-NotesAutoGen")  # opened before Run
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Run", exact=True).click()
        panel = page.get_by_test_id("tc-run")
        expect(panel).to_contain_text("TC-SMOKE-002-NotesAutoGen on dev")
        expect(page.get_by_role("tab", name="Generator")).to_have_attribute("aria-selected", "true")  # no jump
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=180_000)
        results = page.get_by_test_id("tc-run-result")
        expect(results).to_have_count(1)
        expect(results).to_contain_text("passed")
        expect(results).to_contain_text("TC-SMOKE-002-NotesAutoGen")
        steps = panel.get_by_test_id("test-steps")  # the test's steps, with their result and time
        expect(steps).to_contain_text("SmokeHome")
        expect(steps).to_contain_text("passed")

        # a failing one says where it failed, and Details opens the failure
        page.get_by_text("TC-SMOKE-004").first.click()
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-004-MissingElement")  # not the previous one
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Run", exact=True).click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Failed", timeout=180_000)
        expect(page.get_by_test_id("tc-run-result")).to_contain_text("SmokeHome > missing")
        expect(panel.get_by_test_id("test-steps")).to_contain_text("failed")
        page.get_by_test_id("tc-run-details").click()
        details = page.get_by_role("dialog", name="Test details")
        expect(details).to_be_visible()
        expect(details.get_by_test_id("test-steps")).to_contain_text("SmokeHome")  # which step failed, and why
        expect(details.get_by_test_id("test-steps")).to_contain_text("failed")
        page.keyboard.press("Escape")

        page.get_by_test_id("tc-run-control").click()  # the full view is one click away
        expect(page.get_by_role("tab", name="Control")).to_have_attribute("aria-selected", "true")
        browser.close()
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp_path / "runs").glob("*/run.json"))]
    assert [[r["test_id"] for r in run["results"]] for run in runs] == \
        [["TC-SMOKE-002-NotesAutoGen"], ["TC-SMOKE-004-MissingElement"]]
