"""AutoPlay Lite, built into a temporary folder and started from there: three sections, no Recorder or Studio,
no REST API, and a run from Control works end to end."""
from __future__ import annotations

import importlib.util
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build(out: Path) -> None:
    spec = importlib.util.spec_from_file_location("build_lite", REPO_ROOT / "tools" / "build_lite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(out)


def test_lite_has_control_history_and_a_generator_without_recorder_and_studio(workspace: Path, tmp_path: Path):
    lite = tmp_path / "AutomationLite"
    _build(lite)
    problems: list[str] = []
    with running_app(tmp_path, cwd=lite) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: m.type in ("error", "warning") and problems.append(m.text))
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")

        expect(page.locator(".q-tab")).to_have_text(["Control", "Generator", "History"])
        expect(page.locator("header")).to_contain_text("AutoPlay Lite")
        for gone in ("Help", "Schedules", "Journeys"):
            expect(page.get_by_role("tab", name=gone)).to_have_count(0)
        expect(page.get_by_test_id("ai-settings")).to_have_count(0)

        page.get_by_role("tab", name="Generator").click()
        expect(page.get_by_role("tab", name="Pages")).to_be_visible()
        expect(page.get_by_test_id("recorder-switch")).to_have_count(0)
        expect(page.get_by_test_id("record-button")).to_have_count(0)
        expect(page.get_by_test_id("tab-studio")).to_have_count(0)
        expect(page.get_by_test_id("tab-data")).to_be_visible()

        page.get_by_role("tab", name="Control").click()
        expect(page.get_by_test_id("run-formula")).to_contain_text("2 tests")
        page.get_by_test_id("run-button").click()
        expect(page.locator(".any-headline")).to_have_text("Passed", timeout=180_000)
        run_card = page.locator("[aria-labelledby='current-run-title']")
        with page.context.expect_page() as report_page:
            run_card.get_by_test_id("run-report").click()
        expect(report_page.value.get_by_text("AutoPlay run report")).to_be_visible()
        report_page.value.close()

        page.get_by_role("tab", name="History").click()
        expect(page.get_by_role("tab", name="History")).to_have_attribute("aria-current", "page")

        for path in ("docs", "api/v1/runs"):
            try:
                urllib.request.urlopen(url + path, timeout=5)
                raise AssertionError(f"/{path} should not exist in Lite")
            except urllib.error.HTTPError as err:
                assert err.code == 404
        browser.close()
    assert not problems, problems
