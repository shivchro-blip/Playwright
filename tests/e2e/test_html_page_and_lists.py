"""The Generator's "From HTML" dialog, Yes/No rows in the step editor, the folder filters, and Control's big-run
confirmation, driven in a real browser."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app

FIXTURES = Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "opa_pages"


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def smoke_test(test_id: str, description: str = "") -> dict:
    return {"testCaseID": test_id, "system": "SMOKE", "module": "lists", "description": description,
            "tags": ["smoke", test_id], "steps": [{"screen": "SmokeHome", "formData": {"searchBox": "x"}}]}


def open_generator(page, section: str) -> None:
    page.get_by_role("tab", name="Generator").click()
    page.get_by_role("tab", name=section).click()


def test_from_html_builds_the_fields_and_steps_and_adds_them_to_the_page(workspace: Path, tmp_path: Path):
    write(workspace / "Pages" / "SMOKE" / "OpaPage.json", {"path": "/index.html", "fields": {}})
    html = (FIXTURES / "business_associations.html").read_text(encoding="utf-8")
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        open_generator(page, "Pages")
        page.get_by_text("OpaPage", exact=True).first.click()
        expect(page.get_by_role("heading", name="OpaPage", level=2)).to_be_visible()
        page.get_by_test_id("html-open").click()
        dialog = page.get_by_role("dialog", name="Add fields from pasted HTML")
        dialog.get_by_test_id("html-build").click()  # nothing pasted yet
        expect(page.get_by_text("Paste the page's HTML first.")).to_be_visible()
        dialog.get_by_test_id("html-input").fill(html)
        dialog.get_by_test_id("html-build").click()
        expect(dialog.get_by_test_id("html-found")).to_have_text("12 fields found - conventions: OPA (NYBE)")
        notes = dialog.get_by_test_id("html-notes")
        expect(notes).to_contain_text("probably revealed by Franchise")
        expect(notes).to_contain_text("probably revealed by HasTaxPrep")
        expect(dialog.get_by_test_id("html-screen")).to_have_value("OpaPage")  # the page the fields are added to
        steps = dialog.get_by_test_id("html-steps")
        expect(steps.first).to_contain_text('"Franchise_No": "click"')
        expect(steps.first).not_to_contain_text("Franchise_Yes")
        expect(steps.nth(1)).to_contain_text('"Franchise_Yes": "click"')
        expect(steps.nth(1)).to_contain_text('"PreparerDate": null')
        dialog.get_by_label("Franchise_Yes in the step").click()  # the ticks change the first step
        expect(dialog.get_by_label("Franchise_Yes in the step")).to_have_attribute("aria-checked", "true")
        expect(steps.first).to_contain_text('"Franchise_Yes": "click"')  # one answer per question: first one wins
        dialog.get_by_test_id("html-add").click()
        expect(dialog).to_be_hidden()
        page.get_by_role("button", name="Save").click()
        expect(page.get_by_text("unsaved")).to_be_hidden()
        browser.close()
    assert problems == []
    fields = json.loads((workspace / "Pages" / "SMOKE" / "OpaPage.json").read_text(encoding="utf-8"))["fields"]
    assert list(fields)[:3] == ["Franchise_Yes", "Franchise_No", "FranName"] and len(fields) == 12
    assert fields["PreparerDate"] == {"locator": "[data-attributeid='LSI_DTF17_PreparerDate'] input", "type": "text",
                                      "typeKeys": True, "autoGen": "getDateBetween(-30, 0, '%m/%d/%Y')"}
    assert fields["Franchise_No"]["locator"].endswith("label[for^='boolRadioN_']")


def test_from_html_adds_the_steps_to_the_open_test(workspace: Path, tmp_path: Path):
    write(workspace / "Pages" / "SMOKE" / "OpaPage.json", {"path": "/index.html", "fields": {}})
    write(workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Opa.json",
          {"testCases": [smoke_test("TC-OPA-1")]})
    html = (FIXTURES / "experience.html").read_text(encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(url, wait_until="networkidle")
        open_generator(page, "Test cases")
        page.get_by_text("TC-OPA-1").first.click()
        editor = page.get_by_role("region", name="Test case editor")
        expect(editor.get_by_text("Steps", exact=True).first).to_be_visible()
        open_generator(page, "Pages")
        page.get_by_text("OpaPage", exact=True).first.click()
        expect(page.get_by_role("heading", name="OpaPage", level=2)).to_be_visible()
        page.get_by_test_id("html-open").click()
        dialog = page.get_by_role("dialog", name="Add fields from pasted HTML")
        dialog.get_by_test_id("html-input").fill(html)
        dialog.get_by_test_id("html-build").click()
        expect(dialog.get_by_test_id("html-found")).to_contain_text("7 fields found")
        dialog.get_by_test_id("html-add").click()  # the page first, so the step can find its fields
        page.get_by_role("region", name="Page editor").get_by_role("button", name="Save").click()
        expect(page.get_by_role("region", name="Page editor").get_by_text("unsaved")).to_be_hidden()
        page.get_by_test_id("html-open").click()
        dialog = page.get_by_role("dialog", name="Add fields from pasted HTML")
        dialog.get_by_test_id("html-input").fill(html)
        dialog.get_by_test_id("html-build").click()
        dialog.get_by_test_id("html-add-test").click()
        expect(page.get_by_text("Steps added to the open test case")).to_be_visible()
        open_generator(page, "Test cases")
        expect(editor.get_by_text("unsaved")).to_be_visible()
        page.get_by_role("toolbar", name="Test case actions").get_by_role("button", name="Save").click()
        expect(editor.get_by_text("unsaved")).to_be_hidden()
        browser.close()
    steps = json.loads((workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Opa.json").read_text(
        encoding="utf-8"))["testCases"][0]["steps"]
    assert steps[1] == {"screen": "OpaPage", "formData": {
        "ExperienceType_WithinTheUnitedStates": "click", "ExperienceState": "NY", "ExperienceDocType": "CCURTRN",
        "ExperienceDocUpload": None}}
    assert steps[2] == {"commonAction": "Save and continue"}


def test_a_yes_no_question_is_one_row_with_a_choice(workspace: Path, tmp_path: Path):
    write(workspace / "Pages" / "SMOKE" / "Questions.json", {"path": "/index.html", "fields": {
        "Taxed_Yes": {"locator": "#q", "type": "click"}, "Taxed_No": {"locator": "#notes", "type": "click"},
        "Home_Yes": {"locator": "#q", "type": "click"}, "Home_No": {"locator": "#notes", "type": "click"},
        "searchBox": {"locator": "#q", "type": "text"}}})
    write(workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Questions.json", {"testCases": [{
        **smoke_test("TC-Q-1"), "steps": [{"screen": "Questions", "formData": {"Taxed_No": "click"}}]}]})
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(url, wait_until="networkidle")
        open_generator(page, "Test cases")
        page.get_by_text("TC-Q-1").first.click()
        row = page.get_by_test_id("choice-row-Taxed")
        expect(row).to_be_visible()
        expect(row.get_by_role("button", name="No")).to_have_attribute("aria-pressed", "true")
        row.get_by_role("button", name="Yes").click()
        editor = page.get_by_role("region", name="Test case editor")
        expect(editor.get_by_text("unsaved")).to_be_visible()
        # "all page fields": one answer for Home (Taxed is answered), plus the text box
        page.get_by_role("button", name="All 2 page fields").click()
        expect(page.get_by_test_id("choice-row-Home")).to_be_visible()
        expect(page.get_by_test_id("choice-row-Home").get_by_role("button", name="No")).to_have_attribute(
            "aria-pressed", "true")
        page.get_by_role("toolbar", name="Test case actions").get_by_role("button", name="Save").click()
        expect(editor.get_by_text("unsaved")).to_be_hidden()
        browser.close()
    saved = json.loads((workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Questions.json").read_text(
        encoding="utf-8"))["testCases"][0]["steps"][0]["formData"]
    assert saved == {"Taxed_Yes": "click", "Home_No": "click", "searchBox": None}  # the same entries as ever


def test_the_lists_filter_by_folder_and_show_the_description(workspace: Path, tmp_path: Path):
    suite = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite"
    write(suite / "AAA" / "a.json", {"testCases": [smoke_test("TC_AAA_1", "Fills the search box in AAA")]})
    write(suite / "BBB" / "b.json", {"testCases": [smoke_test("TC_BBB_1", "Fills the search box in BBB")]})
    write(workspace / "Pages" / "SMOKE" / "AAA" / "PageA.json", {"fields": {}})
    write(workspace / "Pages" / "SMOKE" / "BBB" / "PageB.json", {"fields": {}})
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(url, wait_until="networkidle")
        open_generator(page, "Test cases")
        expect(page.get_by_text("Fills the search box in AAA")).to_be_visible()
        expect(page.get_by_text("Fills the search box in BBB")).to_be_visible()
        expect(page.get_by_text("TC_AAA_1", exact=True)).to_have_count(1)  # the tag that repeats the ID is not shown
        page.get_by_test_id("test-folder-filter").click()
        page.get_by_role("option", name="BBB (1)").click()
        expect(page.get_by_text("Fills the search box in AAA")).to_be_hidden()
        expect(page.get_by_text("Fills the search box in BBB")).to_be_visible()
        open_generator(page, "Pages")
        page.get_by_test_id("page-folder-filter").click()
        page.get_by_role("option", name="AAA (1)").click()
        expect(page.get_by_role("cell", name="PageA")).to_be_visible()
        expect(page.get_by_role("cell", name="PageB")).to_be_hidden()
        browser.close()


def test_control_filters_by_folder_runs_only_that_folder_and_asks_before_a_big_run(workspace: Path, tmp_path: Path):
    suite = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite"
    write(suite / "AAA" / "a.json", {"testCases": [smoke_test("TC_AAA_1", "First of AAA"),
                                                   smoke_test("TC_AAA_2", "Second of AAA")]})
    write(suite / "BBB" / "b.json", {"testCases": [smoke_test("TC_BBB_1", "Only one in BBB")]})
    with running_app(tmp_path, AUTOPLAY_RUN_CONFIRM_THRESHOLD="4") as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto(url, wait_until="networkidle")
        expect(page.get_by_text("First of AAA")).to_be_visible()  # the description under the name
        # the tag picker offers the tags, not every test ID
        page.locator("#sel-tags").click()
        options = [o.inner_text() for o in page.locator(".q-menu .q-item").all()]
        assert "smoke" in options and "TC_AAA_1" not in options
        page.keyboard.press("Escape")
        # everything the tag matches is more than the threshold: Run asks first
        expect(page.get_by_test_id("run-formula")).to_contain_text("5")  # 2 smoke tests + the 3 of the folders
        page.get_by_test_id("run-button").click()
        confirm = page.get_by_test_id("run-confirm")
        expect(confirm).to_be_visible()
        expect(confirm.get_by_test_id("run-confirm-what")).to_contain_text("5 tests x 1 browser on SMOKE / dev")
        expect(confirm.get_by_test_id("run-confirm-what")).to_contain_text("AAA, BBB")
        confirm.get_by_test_id("run-confirm-cancel").click()
        expect(confirm).to_be_hidden()
        expect(page.get_by_text("Idle")).to_be_visible()  # nothing started
        # one folder: 2 tests, under the threshold, no question, and only those two run
        page.get_by_test_id("folder-filter").click()
        page.get_by_role("option", name="AAA (2)").click()
        expect(page.get_by_test_id("run-formula")).to_contain_text("2")
        page.get_by_test_id("run-button").click()
        expect(page.get_by_test_id("run-confirm")).to_have_count(0)
        expect(page.get_by_text("Run ", exact=False).first).to_be_visible()
        rows = page.locator(".any-run-table, .q-table").nth(1)
        expect(rows).to_contain_text("TC_AAA_1", timeout=120_000)
        expect(rows).to_contain_text("TC_AAA_2", timeout=120_000)
        expect(rows).not_to_contain_text("TC_BBB_1")
        browser.close()
