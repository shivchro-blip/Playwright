"""Ctrl+K finds any section, test case, page, field or run and goes there; the address follows the tab."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def test_palette_jumps_and_the_address_follows_the_tab(workspace: Path, tmp_path: Path):
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        box = page.get_by_test_id("palette-input")
        results = page.get_by_test_id("palette-results")

        # Ctrl+K, a few letters in order, Enter: a section
        page.keyboard.press("Control+k")
        expect(box).to_be_visible()
        expect(results.get_by_role("option").first).to_contain_text("Control")  # empty box: the sections
        box.fill("sched")
        expect(results.get_by_role("option").first).to_contain_text("Schedules")
        box.press("Enter")
        expect(box).to_be_hidden()
        expect(page.get_by_role("tab", name="Schedules")).to_have_attribute("aria-selected", "true")
        expect(page).to_have_url(f"{url}#schedules")

        # a test case opens in the Generator's editor
        page.keyboard.press("Control+k")
        expect(box).to_have_value("")  # each opening starts empty
        box.fill("TC-SMOKE-001")
        expect(results.get_by_role("option").first).to_contain_text("TC-SMOKE-001-Search")
        box.press("Enter")
        expect(page.get_by_role("tab", name="Generator")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-001-Search")

        # a field opens its page with the field picked out; arrows move through the results
        page.get_by_test_id("palette-open").click()
        expect(box).to_have_value("")
        box.fill("searchbox")
        options = results.get_by_role("option")
        expect(options.first).to_contain_text("searchBox")
        expect(options.first).to_have_attribute("aria-selected", "true")
        box.press("ArrowDown")
        box.press("ArrowUp")
        expect(options.first).to_have_attribute("aria-selected", "true")
        box.press("Enter")
        expect(page.get_by_text("Pages/SMOKE/SmokeHome.json")).to_be_visible()
        expect(page.get_by_placeholder("Filter fields")).to_have_value("searchBox")

        # nothing found says so; Esc closes
        page.keyboard.press("Control+k")
        expect(box).to_have_value("")
        box.fill("zzqqxx")
        expect(results).to_contain_text("Nothing matches")
        box.press("Escape")
        expect(box).to_be_hidden()

        # a link to a section opens on it, from inside the app and on a fresh load; Back goes back
        page.goto(f"{url}#history")
        expect(page.get_by_role("tab", name="History")).to_have_attribute("aria-selected", "true")
        page.goto(f"{url}#journeys", wait_until="networkidle")
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("tab", name="Journeys")).to_have_attribute("aria-selected", "true")
        browser.close()
    assert problems == []
