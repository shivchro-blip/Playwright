"""Plain-language steps end to end: lines become steps on real fields (AI assist only for what the rules leave, and
never a field that does not exist), then go into the test case."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app

SUITE = Path("Tests") / "SMOKE" / "MasterRegressionSuite" / "SmokeTests.json"


def test_plain_words_become_steps_on_real_fields(workspace: Path, tmp_path: Path):
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.get_by_test_id("ai-settings").click()  # AI assist on (it talks to the local fake API)
        settings = page.get_by_role("dialog", name="AI assist settings")
        settings.get_by_test_id("ai-switch").click()
        settings.get_by_role("button", name="Close").click()

        page.keyboard.press("Control+k")
        box = page.get_by_test_id("palette-input")
        expect(box).to_have_value("")
        box.fill("TC-SMOKE-001-Search")
        box.press("Enter")
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-001-Search")
        before = len(json.loads((workspace / SUITE).read_text(encoding="utf-8"))["testCases"][0]["steps"])

        page.get_by_test_id("intent-open").click()
        dialog = page.get_by_test_id("intent-dialog")
        dialog.get_by_test_id("intent-text").fill(
            'Open smoke home\nType "dress" into search box\nLook for shoes\nMake up a field')
        dialog.get_by_test_id("intent-compile").click()
        rows = dialog.get_by_test_id("intent-rows")
        expect(rows.locator("tr[data-status=resolved]")).to_have_count(2)
        expect(rows).to_contain_text('SmokeHome.searchBox: type "dress"')
        expect(rows).to_contain_text('SmokeHome.searchBox: type "shoes"')  # from AI assist, on a real field
        expect(rows.locator("tr[data-status=unresolved]")).to_contain_text("does not exist: ignored")
        dialog.get_by_test_id("intent-check").click()
        expect(dialog.get_by_test_id("intent-live")).to_contain_text("SmokeHome.searchBox: found")
        dialog.get_by_test_id("intent-add").click()
        expect(page.get_by_text("unsaved")).to_be_visible()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("unsaved")).to_be_hidden()
        browser.close()
    assert problems == []
    steps = json.loads((workspace / SUITE).read_text(encoding="utf-8"))["testCases"][0]["steps"]
    assert len(steps) == before + 1
    assert steps[-1] == {"screen": "SmokeHome", "do": [{"field": "searchBox", "value": "dress"},
                                                       {"field": "searchBox", "value": "shoes"}]}
