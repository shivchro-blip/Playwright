"""Console tools, what went wrong, keyboard access, run strip, and the Generator keeping your place and your work."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app

HAS_SCREEN = sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_app(p, url: str, width: int = 1440):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 1000})
    page.goto(url, wait_until="networkidle")
    return browser, page


def pick_tags(page, *tags: str, remove: str = "smoke") -> None:
    page.get_by_label(f"Remove tag {remove}").click()
    for tag in tags:
        page.locator("#sel-tags").click()
        page.locator(".q-menu .q-item", has_text=tag).first.click()
    page.keyboard.press("Escape")


# ───────────── the console ─────────────

def test_a_reopened_run_shows_its_whole_log_and_the_tools_find_filter_wrap_and_copy(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["E2E"])  # four tests: two pass, one fails, one is missing a field
    assert len(run.log) > 450  # more than the 400 lines the page used to keep
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        rows = page.locator(".any-console-body .any-cl")
        expect(rows.first).to_be_visible()
        # every line of console.log, not the last 400 (the app's own closing notes are not in that file)
        assert rows.count() > 450 and abs(rows.count() - len(run.log)) <= 8

        tools = page.get_by_role("toolbar", name="Console tools")
        find = tools.get_by_label("Find in the console")
        find.fill("MissingElement")
        count = tools.locator(".any-ct-count")
        expect(count).to_contain_text("1 of")
        expect(page.locator(".any-cl.is-hit-active")).to_have_count(1)
        tools.get_by_role("button", name="Next match").click()
        expect(count).to_contain_text("2 of")
        find.fill("zz-not-in-the-log")
        expect(count).to_have_text("No matches")
        find.fill("")

        total = rows.count()
        tools.get_by_label("Which lines to show").select_option("error")
        expect(tools).to_contain_text("lines hidden by the filter")
        assert 0 < rows.count() < total
        assert page.locator(".any-console-body .any-cl:not(:has(.lvl-ERROR)):not(.cl-fail)").evaluate_all(
            "els => els.filter(e => !/^E\\s{2,}/.test(e.textContent)).length") == 0  # only errors are left
        tools.get_by_label("Which lines to show").select_option("all")
        expect(rows).to_have_count(total)

        wrap = tools.get_by_role("button", name="Wrap lines")
        wrap.click()
        expect(wrap).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".any-console-body.is-wrap")).to_have_count(1)
        copy = tools.locator("button.any-ct-btn").last  # its name changes to "Copied": find it by place
        expect(copy).to_have_text("Copy")
        copy.click()
        expect(copy).to_have_text("Copied")
        # a screen reader is not read every line: the log is not a live region, the status line is
        expect(page.get_by_role("log", name="Console output")).to_have_attribute("aria-live", "off")
        expect(page.locator(".any-headline")).to_have_attribute("aria-live", "polite")
        browser.close()


def test_scrolling_up_in_a_live_console_holds_the_place_and_offers_jump_to_latest(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        page.get_by_test_id("run-button").click()
        body = page.locator(".any-console-body")
        expect(page.locator(".any-console-body .any-cl")).not_to_have_count(0, timeout=60_000)
        page.wait_for_function("document.querySelectorAll('.any-console-body .any-cl').length > 25", timeout=60_000)
        body.evaluate("el => { el.scrollTop = 0; }")
        jump = page.get_by_role("button", name="jump to latest")
        expect(jump).to_be_visible(timeout=60_000)
        assert body.evaluate("el => el.scrollTop") < 40  # the place was held while lines kept arriving
        jump.click()
        expect(jump).to_have_count(0)
        assert body.evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight") < 10
        browser.close()


# ───────────── what went wrong, and the keyboard ─────────────

def test_what_went_wrong_lists_the_failures_and_leads_to_details_test_case_and_log(workspace: Path, tmp_path: Path):
    run_and_wait(workspace, tmp_path, tags=["E2E"])
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        panel = page.get_by_test_id("what-went-wrong")
        expect(panel).to_be_visible()
        failed = panel.get_by_test_id("failed-test")
        expect(failed).to_have_count(2)  # BadAutoGen and MissingElement; the two passes are not listed
        expect(failed.first).to_contain_text("TC-SMOKE-00")
        missing = failed.filter(has_text="MissingElement")
        expect(missing).to_contain_text("SmokeHome")  # where it stopped

        missing.get_by_test_id("fail-show-log").click()  # the console jumps to the test's lines
        expect(page.locator(".any-ct-count")).to_contain_text("1 of")
        missing.get_by_test_id("fail-open-test").click()  # and the Generator opens the test case
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-004-MissingElement")
        browser.close()


def test_failure_details_open_from_the_keyboard_and_focus_comes_back(workspace: Path, tmp_path: Path):
    run_and_wait(workspace, tmp_path, tags=["E2E"])
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        chip = page.get_by_role("button", name="Details of TC-SMOKE-004-MissingElement")  # a real button now
        chip.focus()
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog", name="Test details")
        expect(dialog).to_be_visible()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(chip).to_be_focused()
        page.get_by_test_id("skip-link").focus()  # the first tab stop skips the bar
        expect(page.get_by_test_id("skip-link")).to_be_visible()
        page.keyboard.press("Enter")
        assert page.evaluate("document.activeElement.id") == "any-main"
        expect(page.get_by_test_id("clear-runs")).to_have_text("Delete run…")  # says what it does
        expect(page.get_by_test_id("clear-runs")).to_contain_text("Delete run")
        browser.close()


@pytest.mark.skipif(HAS_SCREEN, reason="needs a machine where a visible browser cannot open")
def test_a_browser_that_cannot_start_is_one_cause_with_a_way_out(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        page.get_by_text("Browser & artifacts").click()
        page.get_by_role("switch", name="Headless").click()  # off: no screen here, so the browser cannot open
        page.get_by_test_id("run-button").click()
        shared = page.get_by_test_id("shared-cause")
        expect(shared).to_contain_text("The browser could not start", timeout=240_000)
        page.get_by_test_id("fix-headless").click()
        expect(page.get_by_role("switch", name="Headless")).to_have_attribute("aria-checked", "true")
        browser.close()


# ───────────── the run strip ─────────────

def test_the_run_strip_follows_a_run_on_every_tab_and_opens_it_in_control(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        strip = page.get_by_test_id("run-strip")
        expect(strip.get_by_role("button", name="Show this run in Control")).to_have_count(0)  # nothing to say
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text("TC-SMOKE-004").first.click()
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Run", exact=True).click()
        page.get_by_role("tab", name="History").click()
        button = strip.get_by_role("button", name="Show this run in Control")
        expect(button).to_contain_text("Running", timeout=60_000)  # a run is going, and this is another tab
        expect(button).to_contain_text("Failed", timeout=240_000)  # and it ended: not missed
        page.get_by_role("tab", name="Control").click()
        expect(button).to_have_count(0)  # Control shows it in full
        page.get_by_role("tab", name="History").click()
        expect(button).to_contain_text("Failed")
        button.click()
        expect(page.get_by_role("tab", name="Control")).to_have_attribute("aria-selected", "true")
        expect(page.locator(".any-headline")).to_have_text("Failed")
        page.get_by_role("tab", name="History").click()
        expect(strip.get_by_role("button", name="Show this run in Control")).to_have_count(0)  # seen: gone
        browser.close()


# ───────────── the Generator ─────────────

def open_test(page, test: str = "TC-SMOKE-002") -> None:
    page.get_by_role("tab", name="Generator").click()
    page.get_by_role("tab", name="Test cases").click()
    page.get_by_text(test).first.click()
    expect(page.locator("#tc-id")).to_have_value(f"{test}-NotesAutoGen" if test.endswith("002") else
                                                 "TC-SMOKE-004-MissingElement")


def test_the_generator_keeps_its_place_between_tabs_and_after_a_reload(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        open_test(page)
        page.get_by_role("tab", name="History").click()
        page.get_by_role("tab", name="Generator").click()
        expect(page.get_by_role("tab", name="Test cases")).to_have_attribute("aria-selected", "true")  # not Pages
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-002-NotesAutoGen")  # and the test is still open

        page.reload(wait_until="networkidle")  # nothing unsaved: no prompt, and it comes back to the same place
        page.get_by_role("tab", name="Generator").click()
        expect(page.get_by_role("tab", name="Test cases")).to_have_attribute("aria-selected", "true")
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-002-NotesAutoGen", timeout=15_000)
        browser.close()


def test_a_reload_with_unsaved_edits_asks_first(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        open_test(page)
        asked: list[str] = []
        page.on("dialog", lambda d: (asked.append(d.type), d.dismiss()))
        page.locator("#tc-desc").fill("an edit that is not saved")
        expect(page.get_by_text("unsaved").first).to_be_visible()
        page.wait_for_function("window.__anyUnsaved === true", timeout=10_000)  # told to the browser within a second
        page.evaluate("setTimeout(() => location.reload(), 0)")  # the dialog is dismissed: the page stays
        page.wait_for_function("true")
        for _ in range(50):
            if asked:
                break
            page.wait_for_timeout(100)
        assert asked == ["beforeunload"]
        expect(page.locator("#tc-desc")).to_have_value("an edit that is not saved")
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_function("window.__anyUnsaved === false", timeout=10_000)
        browser.close()


def test_the_toolbar_leads_with_save_run_and_check_and_problems_are_a_list_with_links(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url, width=1440)
        open_test(page)
        editor = page.get_by_role("region", name="Test case editor")
        bar = editor.get_by_role("toolbar", name="Test case actions")
        for name in ("Check", "More", "Run", "Debug", "From step...", "Save"):
            expect(bar.get_by_role("button", name=name, exact=True)).to_be_visible()
        assert bar.get_by_role("button").count() <= 8  # not thirteen
        for name in ("Delete test case...", "Copy as a new test case...", "Revert unsaved changes"):
            expect(page.get_by_text(name)).to_have_count(0)  # tucked away until More is opened
        page.get_by_test_id("tc-more").click()
        expect(page.get_by_test_id("tc-delete")).to_be_visible()
        expect(page.get_by_test_id("tc-history")).to_be_visible()
        page.keyboard.press("Escape")

        # a warning that names a step has a way to it; an error blocks saving and is not a folded warning
        page.get_by_label("Step 1 target").fill("NoSuchPage")
        page.keyboard.press("Enter")
        bar.get_by_role("button", name="Check").click()
        warnings = page.get_by_test_id("tc-warnings")
        expect(warnings).to_contain_text("page 'NoSuchPage' not found")
        warnings.get_by_role("button", name="Go to step 1").click()
        expect(page.locator("#tc-step-1")).to_have_class(__import__("re").compile(r"is-flash"))
        page.locator("#tc-id").fill("")
        bar.get_by_role("button", name="Check").click()
        expect(page.get_by_test_id("tc-errors")).to_have_text("1 error blocks saving")
        expect(page.get_by_test_id("tc-problems").get_by_role("alert")).to_contain_text("testCaseID is required")
        bar.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_test_id("tc-errors")).to_be_visible()  # a refused save leaves the reasons on the page
        browser.close()


def test_a_run_from_the_generator_says_how_it_will_run_and_links_to_where_that_is_set(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser, page = open_app(p, url)
        open_test(page)
        line = page.get_by_test_id("tc-run-settings")
        expect(line).to_contain_text("Runs on dev in chromium, headless (no window)")
        page.get_by_test_id("tc-change-settings").click()
        expect(page.get_by_role("tab", name="Control")).to_have_attribute("aria-selected", "true")
        browser.close()
