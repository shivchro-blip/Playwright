"""Pause on failure: the browser stays open when a step fails; fix the page, resume in the same browser."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app

DEBUG_TESTS = {"testCases": [{
    "testCaseID": "TC-SMOKE-010-Debug", "description": "Two fields work, the third has a wrong locator.",
    "system": "SMOKE", "module": "smoke", "environment": ["dev"], "tags": ["E2E", "debugtest"],
    "steps": [{"screen": "SmokeHome", "formData": {"searchBox": "Playwright", "notes": "hello", "missing": "x"}}]}]}


def add_debug_test(workspace: Path) -> Path:
    folder = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite"
    (folder / "DebugTests.json").write_text(json.dumps(DEBUG_TESTS), encoding="utf-8")
    return workspace / "Pages" / "SMOKE" / "SmokeHome.json"


def open_debug_test(page, url: str) -> None:
    page.goto(url, wait_until="networkidle")
    page.get_by_role("tab", name="Generator").click()
    page.get_by_role("tab", name="Test cases").click()
    page.get_by_text("TC-SMOKE-010").first.click()
    expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-010-Debug")


def console(tmp_path: Path) -> str:
    return next((tmp_path / "runs").glob("*/console.log")).read_text(encoding="utf-8")


def test_fix_the_page_while_paused_and_the_same_browser_goes_on_from_the_failed_field(workspace: Path, tmp_path: Path):
    page_file = add_debug_test(workspace)
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        open_debug_test(page, url)
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Debug").click()

        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_be_visible(timeout=180_000)  # the field is given 30 s before the step fails
        expect(banner).to_contain_text("Paused: the browser is still open")
        expect(banner).to_contain_text("field 'missing'")
        expect(page.get_by_test_id("debug-error")).to_contain_text("missing")
        expect(banner.locator("img")).to_be_visible()  # the page as it was
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Running")  # still one run, still going

        banner.get_by_test_id("debug-open-page").click()  # jumps to the page to fix
        expect(page.get_by_role("tab", name="Pages")).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("region", name="Page editor")).to_contain_text("SmokeHome")

        fixed = json.loads(page_file.read_text(encoding="utf-8"))  # what saving the page in the editor writes
        fixed["fields"]["missing"]["locator"] = "#notes"
        page_file.write_text(json.dumps(fixed), encoding="utf-8")

        page.get_by_role("tab", name="Test cases").click()
        page.get_by_test_id("debug-retry").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=180_000)
        expect(page.get_by_test_id("debug-banner")).to_have_count(0)
        browser.close()
    log = console(tmp_path)
    assert log.count("Performing FILL on 'searchBox'") == 1  # it did not start over: no second login or first fields
    assert log.count("Performing FILL on 'notes'") == 1
    assert log.count("Performing FILL on 'missing'") == 2  # the failed attempt and the one after the fix
    assert "[debug] PAUSED at step 1" in log and "[debug] RESUMING (retry) from step 1, in the same browser" in log
    run = json.loads(next((tmp_path / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert run["status"] == "passed" and run["debug_pauses"] == 1  # a clean pass would say 0
    assert [r["outcome"] for r in run["results"]] == ["passed"]


def test_ending_the_test_makes_the_failure_stand(workspace: Path, tmp_path: Path):
    add_debug_test(workspace)
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        open_debug_test(page, url)
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Debug").click()
        expect(page.get_by_test_id("debug-banner")).to_be_visible(timeout=180_000)
        page.get_by_test_id("debug-abort").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Failed", timeout=120_000)
        expect(page.get_by_test_id("debug-banner")).to_have_count(0)
        browser.close()
    run = json.loads(next((tmp_path / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert run["status"] == "failed" and [r["outcome"] for r in run["results"]] == ["failed"]


def test_a_normal_run_of_the_same_failing_test_still_fails_without_waiting(workspace: Path, tmp_path: Path):
    add_debug_test(workspace)
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        open_debug_test(page, url)
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Run", exact=True).click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Failed", timeout=180_000)
        assert page.get_by_test_id("debug-banner").count() == 0
        browser.close()
    assert "[debug]" not in console(tmp_path)


def test_fixing_a_common_action_while_paused(workspace: Path, tmp_path: Path):
    """The edited common action is what runs next, in the same browser, from the screen that failed."""
    folder = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite"
    case = {"testCases": [{
        "testCaseID": "TC-SMOKE-011-CommonThenPage", "description": "A common action that fails on its 2nd field.",
        "system": "SMOKE", "module": "smoke", "environment": ["dev"], "tags": ["E2E", "debugtest"],
        "steps": [{"commonAction": "fill the form"}]}]}
    (folder / "DebugTests.json").write_text(json.dumps(case), encoding="utf-8")
    common_file = workspace / "Tests" / "com" / "commonActions.json"
    common = {"commonActions": {"fill the form": {"steps": [
        {"screen": "SmokeHome", "formData": {"searchBox": "Playwright"}},
        {"screen": "SmokeHome", "formData": {"notes": "first", "missing": "x"}}]}}}
    common_file.write_text(json.dumps(common), encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text("TC-SMOKE-011").first.click()
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Debug").click()
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_be_visible(timeout=180_000)
        expect(banner).to_contain_text("field 'missing'")

        common["commonActions"]["fill the form"]["steps"][1]["formData"] = {"notes": "second", "phone": None}
        common_file.write_text(json.dumps(common), encoding="utf-8")  # the fix: the common action's 2nd screen
        page.get_by_test_id("debug-retry").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=180_000)
        browser.close()
    log = console(tmp_path)
    assert log.count("Performing FILL on 'searchBox'") == 1  # the common action's 1st screen did not run again
    assert log.count("Performing FILL on 'phone'") == 1 and "[debug] RESUMING (retry) from step 1" in log


def test_fixing_the_test_case_itself_while_paused(workspace: Path, tmp_path: Path):
    """The test case as saved now is what runs next: the failed field is replaced, earlier ones are not repeated."""
    add_debug_test(workspace)
    test_file = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "DebugTests.json"
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        open_debug_test(page, url)
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Debug").click()
        expect(page.get_by_test_id("debug-banner")).to_be_visible(timeout=180_000)

        fixed = json.loads(json.dumps(DEBUG_TESTS))  # the save in the editor: the 3rd field is another one
        fixed["testCases"][0]["steps"][0]["formData"] = {"searchBox": "Playwright", "notes": "hello", "phone": None}
        test_file.write_text(json.dumps(fixed), encoding="utf-8")
        page.get_by_test_id("debug-retry").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=180_000)
        browser.close()
    log = console(tmp_path)
    assert log.count("Performing FILL on 'searchBox'") == 1 and log.count("Performing FILL on 'phone'") == 1


def test_a_test_file_that_is_broken_while_paused_is_reported_and_the_run_keeps_waiting(workspace: Path, tmp_path: Path):
    add_debug_test(workspace)
    test_file = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "DebugTests.json"
    good = test_file.read_text(encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        open_debug_test(page, url)
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Debug").click()
        expect(page.get_by_test_id("debug-banner")).to_be_visible(timeout=180_000)
        test_file.write_text(good[:-5], encoding="utf-8")  # half-saved JSON
        page.get_by_test_id("debug-retry").click()
        expect(page.get_by_test_id("debug-notice")).to_contain_text("Could not read the edited files", timeout=60_000)
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Running")  # still waiting, nothing crashed
        page.get_by_test_id("debug-abort").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Failed", timeout=120_000)
        browser.close()


def test_pause_on_failure_from_the_control_tab(workspace: Path, tmp_path: Path):
    add_debug_test(workspace)
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_label("Remove tag smoke").click()  # only the debug test
        page.locator("#sel-tags").click()
        page.locator(".q-menu .q-item", has_text="debugtest").click()
        page.keyboard.press("Escape")
        page.get_by_text("Browser & artifacts").click()
        page.get_by_test_id("sw-debug").click()
        page.get_by_test_id("run-button").click()
        expect(page.locator(".any-headline")).to_have_text("Paused: waiting for you", timeout=180_000)
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_contain_text("field 'missing'")
        expect(page.get_by_test_id("cancel-run")).to_be_visible()  # the whole run can still be cancelled
        page.get_by_test_id("debug-abort").click()
        expect(page.locator(".any-headline")).to_have_text("Failed", timeout=120_000)
        expect(page.get_by_test_id("debug-banner")).to_have_count(0)
        page.get_by_role("button", name="Save as defaults").click()  # never saved as a default
        browser.close()
    assert "debugOnFailure" not in (tmp_path / "ui.env").read_text(encoding="utf-8")


def test_continue_in_studio_adds_the_next_page_to_the_paused_test_case(workspace: Path, tmp_path: Path):
    """From a paused test: the Studio takes over its browser, a page is captured there, and its steps are inserted
    into the test case that paused, without running the test from its first step again."""
    import re

    add_debug_test(workspace)
    test_file = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "DebugTests.json"
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        open_debug_test(page, url)
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Debug").click()
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_be_visible(timeout=180_000)
        port = banner.get_attribute("data-cdp")
        peek = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        live = next(pg for ctx in peek.contexts for pg in ctx.pages if not pg.is_closed())
        width = live.evaluate("innerWidth")
        box = live.locator("#notes").bounding_box()
        peek.close()

        page.get_by_test_id("debug-studio").click()
        expect(page.get_by_role("tab", name="Studio")).to_have_attribute("aria-selected", "true")
        picture = page.get_by_test_id("studio-picture").locator("img")
        expect(picture).to_have_attribute("src", re.compile("^data:image/jpeg"), timeout=30_000)
        shown = picture.bounding_box()
        scale = shown["width"] / width
        picture.click(position={"x": (box["x"] + box["width"] / 2) * scale, "y": (box["y"] + box["height"] / 2) * scale})
        expect(page.get_by_test_id("studio-picked")).to_contain_text("#notes")
        page.get_by_test_id("studio-value").fill("typed while paused")
        page.get_by_test_id("studio-run").click()
        expect(page.get_by_test_id("studio-steps").locator("li")).to_have_count(1)

        page.get_by_test_id("studio-page").fill("NextPage")
        page.keyboard.press("Enter")
        expect(page.get_by_test_id("studio-after")).to_have_value("1")  # after the step that paused
        page.get_by_test_id("studio-save").click()
        expect(page.get_by_text("added its steps to the test case after step 1")).to_be_visible(timeout=15_000)

        page.get_by_role("tab", name="Test cases").click()
        page.get_by_test_id("debug-abort").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Failed", timeout=120_000)
        browser.close()

    steps = json.loads(test_file.read_text(encoding="utf-8"))["testCases"][0]["steps"]
    assert [s["screen"] for s in steps] == ["SmokeHome", "NextPage"]
    assert list(steps[1]["formData"].values()) == ["typed while paused"]
    assert (workspace / "Pages" / "SMOKE" / "NextPage.json").is_file()
