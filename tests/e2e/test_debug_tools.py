"""The debugging tools around a paused test: sign-in pauses, stepping through, fix on the live page (check, suggest,
pick, add a field), save state, keep waiting, files saved since, fast fail, and a run that paused kept out of stats."""
from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_debug_pause import add_debug_test, console, open_debug_test
from tests.e2e.test_ui import running_app

TWO_STEPS = {"testCases": [{
    "testCaseID": "TC-SMOKE-020-TwoSteps", "description": "Step 1 works, step 2 has a wrong locator.",
    "system": "SMOKE", "module": "smoke", "environment": ["dev"], "tags": ["E2E", "debugtest"],
    "steps": [{"screen": "SmokeHome", "formData": {"searchBox": "Playwright"}},
              {"screen": "SmokeHome", "formData": {"notes": "hello", "missing": "x"}}]}]}


def add_two_steps(workspace: Path) -> Path:
    folder = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite"
    (folder / "DebugTests.json").write_text(json.dumps(TWO_STEPS), encoding="utf-8")
    return workspace / "Pages" / "SMOKE" / "SmokeHome.json"


def open_test(page, number: str) -> None:
    page.get_by_role("tab", name="Generator").click()
    page.get_by_role("tab", name="Test cases").click()
    page.get_by_text(f"TC-SMOKE-{number}").first.click()
    page.wait_for_function(
        f"document.querySelector('#tc-id') && document.querySelector('#tc-id').value.includes('TC-SMOKE-{number}')")


def start_debug(page) -> None:
    page.get_by_role("region", name="Test case editor").get_by_role("button", name="Debug").click()


def click_on_picture(page, port: int, selector: str) -> None:
    """Click, on the picture of the paused page, where `selector` is on that page (asked of the browser itself)."""
    with sync_playwright() as p2:
        remote = p2.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            target = next(pg for ctx in remote.contexts for pg in ctx.pages if not pg.is_closed())
            box = target.locator(selector).first.bounding_box()
        finally:
            remote.close()
    assert box, f"{selector} is not on the paused page"
    picture = page.get_by_test_id("fix-picture")
    shown = picture.bounding_box()
    natural = picture.locator("img").first.evaluate("e => [e.naturalWidth, e.naturalHeight]")
    x = shown["x"] + (box["x"] + box["width"] / 2) * shown["width"] / natural[0]
    y = shown["y"] + (box["y"] + box["height"] / 2) * shown["height"] / natural[1]
    page.mouse.click(x, y)


# ───────────── sign-in ─────────────

def test_a_failed_sign_in_pauses_and_goes_on_once_the_system_data_is_fixed(workspace: Path, tmp_path: Path):
    add_debug_test(workspace)
    system_file = workspace / "Data" / "systemData.json"
    good = json.loads(system_file.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(good))
    del broken["systems"]["SMOKE"]["dev"]["logged_in_check"]  # the sign-in has nothing to verify with
    system_file.write_text(json.dumps(broken), encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        open_debug_test(page, url)
        start_debug(page)
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_be_visible(timeout=120_000)
        expect(banner).to_contain_text("Paused: signing in failed")
        expect(page.get_by_test_id("debug-error")).to_contain_text("logged_in_check")
        expect(page.get_by_test_id("debug-retry")).to_have_text("Try signing in again")
        expect(page.get_by_test_id("debug-skip")).to_have_count(0)  # nothing to skip in a sign-in
        system_file.write_text(json.dumps(good), encoding="utf-8")  # the fix
        page.get_by_test_id("debug-retry").click()
        # the test goes on and meets its own failure (the wrong locator): a second pause, now in the steps
        expect(page.get_by_test_id("debug-banner")).to_contain_text("field 'missing'", timeout=120_000)
        page.get_by_test_id("debug-abort").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Failed", timeout=120_000)
        browser.close()
    log = console(tmp_path)
    assert "[debug] PAUSED at step 1 (Sign-in)" in log and "RESUMING (retry) from step 1" in log


# ───────────── stepping through ─────────────

def test_walk_a_test_one_step_at_a_time_run_skip_and_go_on(workspace: Path, tmp_path: Path):
    add_two_steps(workspace)
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        open_test(page, "020")
        page.get_by_test_id("tc-more").click()
        page.get_by_test_id("tc-step-through").click()
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_contain_text("Paused before step 1: SmokeHome", timeout=120_000)
        expect(page.get_by_test_id("debug-error")).to_contain_text("Nothing has failed")
        expect(page.get_by_test_id("debug-go")).to_be_visible()
        page.get_by_test_id("debug-retry").click()  # run this step
        expect(banner).to_contain_text("Paused before step 2", timeout=120_000)
        expect(page.get_by_test_id("debug-retry")).to_have_text("Run this step")
        page.get_by_test_id("debug-skip").click()  # leave step 2 (the broken one) out
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=120_000)
        browser.close()
    log = console(tmp_path)
    assert "RUNNING step 1 (retry)" in log and "SKIPPING step 2" in log
    assert log.count("Performing FILL on 'searchBox'") == 1 and "Performing FILL on 'missing'" not in log
    run = json.loads(next((tmp_path / "runs").glob("*/run.json")).read_text(encoding="utf-8"))
    assert run["status"] == "passed" and run["debug_pauses"] == 2  # two stops, and the run says it is not clean


def test_run_to_the_end_stops_asking(workspace: Path, tmp_path: Path):
    add_two_steps(workspace)
    test_file = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "DebugTests.json"
    fixed = json.loads(json.dumps(TWO_STEPS))
    fixed["testCases"][0]["steps"][1]["formData"] = {"notes": "hello"}  # nothing broken: it can run through
    test_file.write_text(json.dumps(fixed), encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        open_test(page, "020")
        page.get_by_test_id("tc-more").click()
        page.get_by_test_id("tc-step-through").click()
        expect(page.get_by_test_id("debug-banner")).to_contain_text("before step 1", timeout=120_000)
        page.get_by_test_id("debug-go").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=120_000)  # no second stop
        browser.close()
    assert console(tmp_path).count("PAUSED") == 1


# ───────────── the tools on a paused test ─────────────

def test_fix_the_locator_on_the_live_page_then_run_again_and_mark_the_run(workspace: Path, tmp_path: Path):
    page_file = add_debug_test(workspace)
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1400})
        open_debug_test(page, url)
        start_debug(page)
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_be_visible(timeout=120_000)
        port = int(banner.get_attribute("data-cdp"))
        assert port > 0  # the browser was started so the app can look at it

        banner.get_by_test_id("debug-fix").click()
        dialog = page.get_by_role("dialog", name="Fix on the live page")
        expect(dialog.get_by_test_id("fix-picture")).to_be_visible()
        dialog.get_by_test_id("fix-check").click()  # the field's locator, against the page as the test left it
        expect(dialog.get_by_test_id("fix-check-result")).to_contain_text("No matches", timeout=60_000)
        dialog.get_by_test_id("fix-suggest").click()
        expect(dialog.get_by_test_id("fix-results")).to_be_visible()

        click_on_picture(page, port, "#notes")  # pick what the test should have used
        picked = dialog.get_by_test_id("fix-picked")
        expect(picked).to_be_visible(timeout=60_000)
        expect(picked).to_contain_text("notes")
        dialog.get_by_test_id("fix-use-picked").click()
        saved = json.loads(page_file.read_text(encoding="utf-8"))
        assert "notes" in json.dumps(saved["fields"]["missing"]) and saved["fields"]["missing"]["type"] == "text"
        dialog.get_by_test_id("fix-close").click()

        page.get_by_test_id("debug-retry").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=120_000)
        expect(page.get_by_test_id("debug-note")).to_contain_text("not a clean result")
        page.get_by_role("tab", name="History").click()
        expect(page.locator("tbody tr").first).to_contain_text("debugged")
        browser.close()
    log = console(tmp_path)
    assert log.count("Performing FILL on 'searchBox'") == 1  # the earlier fields did not run again
    assert "within 5000ms" in log  # a debug run gave up on the missing element after 5 seconds, not 30


def test_add_a_field_from_the_live_page_before_the_failed_one(workspace: Path, tmp_path: Path):
    add_debug_test(workspace)
    test_file = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "DebugTests.json"
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1400})
        open_debug_test(page, url)
        start_debug(page)
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_be_visible(timeout=120_000)
        port = int(banner.get_attribute("data-cdp"))
        banner.get_by_test_id("debug-fix").click()
        dialog = page.get_by_role("dialog", name="Fix on the live page")
        click_on_picture(page, port, "#phone")
        expect(dialog.get_by_test_id("fix-picked")).to_be_visible(timeout=60_000)
        dialog.get_by_test_id("fix-new-name").fill("extraPhone")
        dialog.get_by_test_id("fix-new-value").fill("555-0100")
        dialog.get_by_test_id("fix-add").click()
        # the run went on with the new field, and met the wrong locator again: a new pause, and the file shows it
        expect(page.get_by_test_id("debug-banner")).to_be_visible()
        saved = json.loads(test_file.read_text(encoding="utf-8"))["testCases"][0]["steps"][0]["formData"]
        assert list(saved) == ["searchBox", "notes", "extraPhone", "missing"] and saved["extraPhone"] == "555-0100"
        page.get_by_test_id("debug-abort").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Failed", timeout=120_000)
        browser.close()
    log = console(tmp_path)
    assert log.count("Performing FILL on 'searchBox'") == 1 and log.count("Performing FILL on 'extraPhone'") == 1


def test_save_the_state_keep_waiting_and_see_what_was_saved_since(workspace: Path, tmp_path: Path):
    page_file = add_two_steps(workspace)
    with running_app(tmp_path, AUTOPLAY_DEBUG_TIMEOUT_MINUTES="4") as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1400})
        page.goto(url, wait_until="networkidle")
        open_test(page, "020")
        start_debug(page)
        banner = page.get_by_test_id("debug-banner")
        expect(banner).to_contain_text("field 'missing'", timeout=120_000)

        expect(page.get_by_test_id("debug-countdown")).to_contain_text("Waits 3:")  # 4 minutes, and counting down
        expect(page.get_by_test_id("debug-extend")).to_be_visible()  # the last 5 minutes: offer to wait longer
        expect(page.get_by_test_id("debug-changed")).to_contain_text("Nothing saved since the pause yet")

        page.get_by_test_id("debug-savestate").click()  # keep the browser as it is for "Run from step 2"
        expect(page.get_by_test_id("debug-info")).to_contain_text("State saved: Run from step 2", timeout=30_000)
        checkpoints = list((tmp_path / "runs").glob("*/checkpoints/*/1.json"))
        assert len(checkpoints) == 1  # the state after step 1, where step 2 can start

        page.get_by_test_id("debug-extend").click()
        expect(page.get_by_test_id("debug-info")).to_contain_text("Waiting another 4 minutes", timeout=30_000)
        expect(page.get_by_test_id("debug-countdown")).to_contain_text("Waits 3:")

        fixed = json.loads(page_file.read_text(encoding="utf-8"))
        fixed["fields"]["missing"]["locator"] = "#notes"
        page_file.write_text(json.dumps(fixed), encoding="utf-8")
        expect(page.get_by_test_id("debug-changed")).to_contain_text("SmokeHome.json", timeout=10_000)
        page.get_by_test_id("debug-retry").click()
        expect(page.get_by_test_id("tc-run-status")).to_have_text("Passed", timeout=120_000)
        browser.close()


# ───────────── the Control tab follows a run started elsewhere ─────────────

def test_control_shows_a_run_started_from_the_generator_without_being_opened(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        expect(page.locator(".any-headline")).to_have_text("Idle")
        open_test(page, "002")
        page.get_by_role("region", name="Test case editor").get_by_role("button", name="Run", exact=True).click()
        page.get_by_role("tab", name="Control").click()
        expect(page.locator(".any-headline")).to_have_text(re.compile("Running|Queued|Passed"), timeout=60_000)
        expect(page.locator(".any-headline")).to_have_text("Passed", timeout=180_000)  # and follows it to the end
        browser.close()
