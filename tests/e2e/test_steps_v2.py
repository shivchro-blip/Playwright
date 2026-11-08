"""Steps v2 end to end: explicit "do" steps and common action parameters run for real, and the editor keeps v2."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app

SUITE = Path("Tests") / "SMOKE" / "MasterRegressionSuite"


def write(workspace: Path) -> Path:
    (workspace / "Tests" / "com").mkdir(parents=True, exist_ok=True)
    (workspace / "Tests" / "com" / "commonActions.json").write_text(json.dumps({"commonActions": {
        "type notes": {"params": {"text": "the default"},
                       "steps": [{"screen": "SmokeHome", "do": [{"field": "notes", "value": "{{text}}"}]}]}}}),
        encoding="utf-8")
    path = workspace / SUITE / "V2.json"
    path.write_text(json.dumps({"testCases": [
        {"testCaseID": "TC-V2-1", "system": "SMOKE", "module": "v2", "tags": ["v2"], "steps": [
            {"screen": "SmokeHome", "do": [{"field": "searchBox", "value": "first"},
                                           {"field": "notes", "value": "a note"},
                                           {"field": "searchBox", "value": "second"},
                                           {"field": "searchBox", "press": "Tab"}]},
            {"commonAction": "type notes", "with": {"text": "from the parameter"}},
            {"commonAction": "type notes"}]}]}, indent=2), encoding="utf-8")
    return path


def test_v2_steps_and_parameters_run(workspace: Path, tmp_path: Path):
    write(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["v2"])
    log = "\n".join(run.log)
    assert run.status == "passed", "\n".join(run.log[-60:])
    assert "from the parameter" in log and "the default" in log  # each call with its own values
    assert "{{text}}" not in log


def test_the_editor_shows_v2_steps_as_rows_and_keeps_them_v2(workspace: Path, tmp_path: Path):
    path = write(workspace)
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.keyboard.press("Control+k")
        box = page.get_by_test_id("palette-input")
        expect(box).to_have_value("")
        box.fill("TC-V2-1")
        box.press("Enter")
        expect(page.locator("#tc-id")).to_have_value("TC-V2-1")
        expect(page.get_by_test_id("tc-format")).to_contain_text("explicit")
        editor = page.get_by_role("region", name="Test case editor")
        expect(editor.locator("input[value='second']")).to_have_count(1)  # the repeated field is its own row
        page.get_by_test_id("tc-format").click()
        expect(page.get_by_test_id("tc-format")).to_contain_text("classic")
        page.get_by_test_id("tc-format").click()  # and back: explicit
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("unsaved")).to_be_hidden()
        browser.close()
    assert problems == []
    step = json.loads(path.read_text(encoding="utf-8"))["testCases"][0]["steps"][0]
    assert step == {"screen": "SmokeHome", "do": [{"field": "searchBox", "value": "first"},
                                                  {"field": "notes", "value": "a note"},
                                                  {"field": "searchBox", "value": "second"},
                                                  {"field": "searchBox", "press": "Tab"}]}
