"""A failed test says where it failed: screen, action and field, with the screenshot, in the app and the report."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import outcomes, run_and_wait
from tests.e2e.test_ui import running_app
from webapp.services.run_artifacts import RunArtifacts
from webapp.services.run_insights import build_report, failures_by_screen


def test_failed_at_in_details_table_and_report(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["missing"])  # the missing-element test fails on purpose
    assert outcomes(run) == {"TC-SMOKE-004-MissingElement": "failed"}, "\n".join(run.log[-40:])

    details = RunArtifacts("/runs").details(run.id, run.results_dir)
    point = details[("TC-SMOKE-004-MissingElement", "chromium")].failure
    assert (point.screen, point.action, point.field_name) == ("SmokeHome", "FILL", "missing")
    assert point.short == "SmokeHome > missing (FILL)" and point.screenshot is not None
    assert failures_by_screen(run, details) == [("SmokeHome", ["TC-SMOKE-004-MissingElement"])]

    report = build_report(run, [run], details)
    assert "Failures by screen" in report and "Failed at" in report and "FILL missing" in report

    with running_app(tmp_path) as url, sync_playwright() as p:  # the app shows the latest run on load
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        row = page.locator("tbody tr", has_text="MissingElement").first
        expect(row).to_contain_text("SmokeHome > missing (FILL)")
        row.click()
        dialog = page.get_by_role("dialog", name="Test details")
        failed_at = dialog.get_by_test_id("failed-at")
        expect(failed_at).to_contain_text("Screen SmokeHome")
        expect(failed_at).to_contain_text("FILL missing")
        expect(failed_at.locator("img")).to_be_visible()
        browser.close()
