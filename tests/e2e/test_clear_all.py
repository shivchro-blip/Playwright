"""Clear all (app bar): confirm, delete previous runs' data from disk, empty Control, Generator and History."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_clear_runs import run_dirs
from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app


def test_clear_all_empties_the_disk_and_every_tab_but_keeps_settings_and_tests(workspace: Path, tmp_path: Path):
    run_and_wait(workspace, tmp_path, tags=["smoke"])
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        expect(page.locator(".any-headline")).to_have_text("Passed")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text("TC-SMOKE-002").first.click()
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Run", exact=True).click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=180_000)
        assert len(run_dirs(tmp_path)) == 2

        page.get_by_test_id("clear-all").click()  # cancelling deletes nothing
        dialog = page.get_by_role("alertdialog", name="Clear all")
        expect(dialog.get_by_test_id("clear-all-list")).to_contain_text("2 runs")
        expect(dialog).to_contain_text("It cannot be undone")
        expect(dialog).to_contain_text("Kept:")
        dialog.get_by_test_id("clear-all-cancel").click()
        assert len(run_dirs(tmp_path)) == 2

        page.get_by_test_id("clear-all").click()
        page.get_by_role("alertdialog", name="Clear all").get_by_test_id("clear-all-confirm").click()
        expect(page.get_by_test_id("clear-all-result")).to_have_text("Cleared")
        expect(page.get_by_role("alertdialog", name="Clear all finished")).to_contain_text("Deleted 2 runs")
        page.get_by_test_id("clear-all-close").click()

        assert run_dirs(tmp_path) == []  # gone from disk, not just hidden
        expect(page.get_by_test_id("tc-run")).to_have_count(0)  # the Generator's run and console
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-002-NotesAutoGen")  # the open test is untouched
        page.get_by_role("tab", name="Control").click()
        expect(page.locator(".any-headline")).to_have_text("Idle")
        expect(page.locator(".any-console-body .any-cl")).to_have_count(0)
        expect(page.locator("#sel-system")).to_be_visible()
        page.get_by_role("tab", name="History").click()
        expect(page.get_by_text("No runs").first).to_be_visible()

        page.get_by_test_id("clear-all").click()  # nothing left: says so instead of a confirmation
        expect(page.get_by_text("Nothing to clear")).to_be_visible()
        browser.close()
    assert (workspace / "Data" / "systemData.json").exists()
    assert (workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "SmokeTests.json").exists()
