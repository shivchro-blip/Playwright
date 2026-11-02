"""Clear: delete the logs and reports of the run on show, or of every finished run, from Control and History."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app


def run_dirs(tmp_path: Path) -> list[str]:
    runs = tmp_path / "runs"
    return sorted(p.name for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else []


def test_clear_this_run_in_control_and_all_runs_in_history(workspace: Path, tmp_path: Path):
    first = run_and_wait(workspace, tmp_path, tags=["smoke"])
    second = run_and_wait(workspace, tmp_path, tags=["smoke"])
    assert run_dirs(tmp_path) == sorted([first.id, second.id])
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        expect(page.locator(".any-headline")).to_have_text("Passed")  # the latest run is on show

        page.get_by_test_id("clear-runs").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_test_id("clear-scope")).to_contain_text(f"Only this run ({second.id})")
        expect(dialog.get_by_test_id("clear-scope")).to_contain_text("All 2 finished runs")
        dialog.get_by_test_id("clear-confirm").click()
        expect(page.locator(".any-headline")).to_have_text("Idle")  # the card is empty, ready for the next run
        assert run_dirs(tmp_path) == [first.id]  # only the shown run went; its logs and reports are gone

        page.get_by_role("tab", name="History").click()
        page.locator("tbody tr").first.click()
        page.get_by_test_id("clear-runs").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_text("All 1 finished run").click()
        dialog.get_by_test_id("clear-confirm").click()
        expect(page.get_by_text("Cleared 1 run")).to_be_visible()
        browser.close()
    assert run_dirs(tmp_path) == []
