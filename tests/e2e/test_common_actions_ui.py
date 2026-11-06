"""Common actions tab: create, edit, check, save, see who calls it, rename everywhere, refuse to delete a called one."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import outcomes, run_and_wait
from tests.e2e.test_ui import running_app

SUITE = Path("Tests") / "SMOKE" / "MasterRegressionSuite"
ACTION = {"steps": [{"screen": "SmokeHome", "formData": {"searchBox": "from a common action"}}]}


def set_code(page, editor, content: str) -> None:
    """Replace the whole editor text (select all, then insert: no auto-closing brackets, no leftovers)."""
    expect(editor).to_contain_text('"steps"')  # the editor holds the action's JSON before it is replaced
    editor.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.insert_text(content)


def calling_test(name: str) -> dict:
    return {"testCases": [{"testCaseID": "TC-COMMON-001", "description": "Calls a common action.", "system": "SMOKE",
                           "module": "common", "environment": ["dev"], "tags": ["common"],
                           "steps": [{"commonAction": name}]}]}


def test_a_common_action_written_in_the_tab_format_runs(workspace: Path, tmp_path: Path):
    (workspace / "Tests" / "com" / "commonActions.json").write_text(
        json.dumps({"commonActions": {"fill search": ACTION}}), encoding="utf-8")
    (workspace / SUITE / "Common.json").write_text(json.dumps(calling_test("fill search")), encoding="utf-8")
    run = run_and_wait(workspace, tmp_path, tags=["common"], options={"headlessMode": True})
    assert run.status == "passed" and outcomes(run) == {"TC-COMMON-001": "passed"}, "\n".join(run.log[-30:])


def test_create_edit_save_rename_and_delete(workspace: Path, tmp_path: Path):
    (workspace / SUITE / "Common.json").write_text(json.dumps(calling_test("fill search")), encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_test_id("tab-common").click()
        expect(page.get_by_text("No common actions yet")).to_be_visible()
        page.get_by_test_id("common-new").click()
        page.get_by_test_id("common-name").fill("fill search")
        page.get_by_test_id("common-create").click()
        expect(page.get_by_role("heading", name="fill search", level=2)).to_be_visible()
        expect(page.get_by_test_id("common-used")).to_contain_text("Called 1 time by TC-COMMON-001")

        editor = page.get_by_test_id("common-code").locator(".cm-content")
        set_code(page, editor, json.dumps(ACTION, indent=2))
        expect(page.get_by_text("unsaved")).to_be_visible()  # the app has seen the edit
        page.get_by_test_id("common-save").click()
        expect(page.get_by_text("Saved fill search")).to_be_visible()
        expect(page.get_by_test_id("common-params")).to_contain_text("Values a test can change (1)")
        page.get_by_test_id("common-params").click()
        expect(page.get_by_text("SmokeHome|searchBox|<value>")).to_be_visible()

        set_code(page, editor, json.dumps({"steps": [{"screen": "SmokeHome", "formData": {"nope": "x"}}]}))
        page.get_by_test_id("common-check").click()
        expect(page.get_by_text("SmokeHome' has no field nope")).to_be_visible()
        set_code(page, editor, '{"steps": [5]}')
        expect(page.get_by_text("unsaved")).to_be_visible()
        page.get_by_test_id("common-save").click()
        expect(page.get_by_text("Not saved: Step 1: must be an object.")).to_be_visible()

        page.get_by_role("button", name="Revert").click()  # a rename needs a saved action
        expect(page.get_by_test_id("common-params")).to_be_visible()
        page.get_by_test_id("common-rename").click()
        page.get_by_test_id("common-rename-name").fill("search once")
        expect(page.get_by_test_id("common-rename-preview")).to_contain_text("1 call in SMOKE/Common.json")
        page.get_by_test_id("common-rename-ok").click()
        expect(page.get_by_role("heading", name="search once", level=2)).to_be_visible()

        page.get_by_role("button", name="Delete common action").click()
        page.get_by_role("button", name="Delete", exact=True).click()
        expect(page.get_by_text("is called by TC-COMMON-001")).to_be_visible()
        browser.close()
    called = json.loads((workspace / SUITE / "Common.json").read_text(encoding="utf-8"))
    assert called["testCases"][0]["steps"] == [{"commonAction": "search once"}]
    saved = json.loads((workspace / "Tests" / "com" / "commonActions.json").read_text(encoding="utf-8"))
    assert saved["commonActions"] == {"search once": ACTION}
