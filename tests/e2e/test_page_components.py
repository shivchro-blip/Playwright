"""Shared page components end to end: one component used twice on a page runs for real, each copy inside its own
block, and the page editor shows, adds and moves fields into components."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app

ADDRESS = {"component": True, "fields": {"street": {"locator": "input[name=street]", "type": "text"},
                                         "city": {"locator": "input[name=city]", "type": "text"}}}


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def setup(workspace: Path) -> Path:
    pages = workspace / "Pages" / "SMOKE"
    write(pages / "components" / "Address.json", ADDRESS)
    write(pages / "TwoAddresses.json", {
        "path": "/components.html",
        "use": [{"component": "Address", "prefix": "mailing", "within": "#mailing"},
                {"component": "Address", "prefix": "home", "within": "#home"}],
        "fields": {"send": {"locator": "#send", "type": "button"},
                   "summary": {"locator": "#summary", "type": "text",
                               "contains": "mailing=1 Main St, Albany; home=2 Side Rd, Troy"}}})
    write(workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Components.json", {"testCases": [
        {"testCaseID": "TC-COMP-1", "system": "SMOKE", "module": "components", "tags": ["components"], "steps": [
            {"screen": "TwoAddresses", "formData": {
                "mailing.street": "1 Main St", "mailing.city": "Albany", "home.street": "2 Side Rd",
                "home.city": "Troy", "send": "click", "summary": {"type": "confirmtext"}}}]}]})
    return pages


def test_one_component_twice_on_a_page_runs_inside_each_block(workspace: Path, tmp_path: Path):
    setup(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["components"])
    # input[name=street] matches two inputs on the page: only a field looked for inside its block finds one
    assert run.status == "passed", "\n".join(run.log[-60:])


def test_a_missing_component_fails_the_step_and_says_why(workspace: Path, tmp_path: Path):
    pages = setup(workspace)
    (pages / "components" / "Address.json").unlink()
    run = run_and_wait(workspace, tmp_path, tags=["components"])
    assert run.status == "failed"
    assert "Component 'Address' not found" in "\n".join(run.log)


def test_the_page_editor_shows_uses_and_moves_fields_into_components(workspace: Path, tmp_path: Path):
    pages = setup(workspace)
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.keyboard.press("Control+k")
        box = page.get_by_test_id("palette-input")
        expect(box).to_have_value("")
        box.fill("TwoAddresses")
        box.press("Enter")
        expect(page.get_by_role("heading", name="TwoAddresses", level=2)).to_be_visible()

        listed = page.get_by_role("region", name="Pages").locator("tbody tr", has_text="Address").first
        expect(listed).to_contain_text("component")
        section = page.get_by_test_id("page-components")
        expect(section.locator("[data-component=Address]")).to_have_count(2)
        expect(section).to_contain_text("mailing.street")
        expect(section).to_contain_text("inside #home")

        # move the page's own "send" into a new component: previewed, then page and component written together
        page.get_by_test_id("page-maintain").click()
        page.get_by_text("Move fields into a component...").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_test_id("extract-fields").click()
        page.get_by_role("option", name="send").click()
        page.keyboard.press("Escape")
        dialog.get_by_test_id("extract-name").fill("Actions")
        dialog.get_by_test_id("refactor-preview").click()
        expect(dialog.get_by_text("Create Pages/SMOKE/components/Actions.json")).to_be_visible()
        dialog.get_by_test_id("refactor-apply").click()
        expect(section.locator("[data-component=Actions]")).to_have_count(1)

        # open the component from the page: it says who uses it
        section.locator("[data-component=Address]").first.get_by_role("button", name="Address").click()
        expect(page.get_by_role("heading", name="Address", level=2)).to_be_visible()
        expect(page.get_by_test_id("page-components")).to_contain_text("Shared: used by TwoAddresses")
        browser.close()
    assert problems == []
    assert read(pages / "components" / "Actions.json") == {
        "component": True, "fields": {"send": {"locator": "#send", "type": "button"}}}
    used = read(pages / "TwoAddresses.json")
    assert used["use"][-1] == "Actions" and "send" not in used["fields"]
