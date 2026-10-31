"""The test case editor speaks in actions (Click, Choose option, Verify text, ...) and writes the same JSON."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_actions_choose_index_and_open_page(workspace: Path, tmp_path: Path):
    write(workspace / "Pages" / "SMOKE" / "FormsPage.json", {"path": "/forms.html", "fields": {
        "Taxed": {"locator": "[data-attribute-id='Taxed'] button:text-is('{value}')", "type": "button"},
        "answers": {"locator": "#answers", "type": "text", "contains": "taxed=No"},
        "go": {"locator": "#go", "type": "button"},
        "member": {"locator": "input.member", "index": 0, "type": "text", "indexed": True},
    }})
    tests_file = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Editor.json"
    write(tests_file, {"testCases": [{
        "testCaseID": "TC-EDIT-1", "system": "SMOKE", "env": "dev", "module": "m", "tags": ["editor"],
        "steps": [
            {"screen": "FormsPage", "formData": {
                "Taxed": "No", "answers": {"type": "confirmtext"}, "go": None, "member": "Ann"}},
            {"screen": "FormsPage", "index": 1, "formData": {"member": "Bob"}},
        ]}]})
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text("TC-EDIT-1").first.click()

        actions = page.get_by_label("Action", exact=True)
        expect(actions).to_have_count(5)
        expect(actions.nth(0)).to_have_value("Choose option")  # {value} field: the test names the answer
        expect(actions.nth(1)).to_have_value("Verify text")
        expect(actions.nth(2)).to_have_value("Generate a value")
        expect(actions.nth(3)).to_have_value("Fill")
        index = page.get_by_label("Occurrence (index, from 0)")
        expect(index).to_have_count(2)  # both steps are on a page with an indexed field
        expect(index.nth(1)).to_have_value("1")

        actions.nth(2).click()  # Generate -> Click
        page.get_by_role("option", name="Click", exact=True).click()
        expect(actions.nth(2)).to_have_value("Click")
        page.get_by_role("button", name="Save").click()
        expect(page.get_by_text("unsaved")).to_be_hidden()

        saved = json.loads(tests_file.read_text(encoding="utf-8"))["testCases"][0]["steps"]
        assert saved[0]["formData"] == {"Taxed": "No", "answers": {"type": "confirmtext"}, "go": "click",
                                        "member": "Ann"}
        assert saved[1]["index"] == 1 and saved[1]["formData"] == {"member": "Bob"}

        page.get_by_role("button", name="Open this page in the Pages tab").first.click()
        expect(page.get_by_role("tab", name="Pages")).to_have_attribute("aria-selected", "true")
        expect(page.get_by_text("Pages/SMOKE/FormsPage.json")).to_be_visible()
        browser.close()
