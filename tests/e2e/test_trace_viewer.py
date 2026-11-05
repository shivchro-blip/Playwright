"""The failure dialog opens a recorded Playwright trace in the trace viewer (a fake stands in for the window)."""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app


def test_open_trace_button_starts_the_viewer_on_this_machine(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True, "tracing": True})
    traces = list(run.results_dir.glob("*-attachment.attach"))  # Allure keeps a zip it cannot name as .attach
    assert traces, "the run recorded a trace"
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.locator("tbody tr", has_text="MissingElement").first.click()
        dialog = page.get_by_role("dialog", name="Test details")
        dialog.get_by_test_id("open-trace").click()
        expect(page.get_by_text("Opening the trace viewer on this computer")).to_be_visible()
        deadline = time.monotonic() + 15
        while not list(run.dir.glob("trace-viewer/*.opened")) and time.monotonic() < deadline:
            time.sleep(0.2)
        browser.close()
    assert list(run.dir.glob("trace-viewer/*.opened")), "the viewer was started with the trace path"


def test_a_shared_server_offers_the_download_only(workspace: Path, tmp_path: Path):
    run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True, "tracing": True})
    with running_app(tmp_path, AUTOPLAY_HOST="0.0.0.0", AUTOPLAY_INSECURE_NO_AUTH="true",
                     AUTOPLAY_ALLOWED_HOSTS="127.0.0.1") as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.locator("tbody tr", has_text="MissingElement").first.click()
        dialog = page.get_by_role("dialog", name="Test details")
        expect(dialog.get_by_text("Playwright trace (.zip)")).to_be_visible()
        expect(dialog.get_by_test_id("open-trace")).to_have_count(0)
        browser.close()
