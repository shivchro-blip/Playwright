"""The Data sets tab: create a file, fill the grid, save; then give a test case that data set."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def test_create_fill_save_and_use_a_data_set(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_test_id("tab-data").click()

        page.get_by_test_id("data-new").click()
        page.get_by_test_id("data-new-name").fill("people")
        page.get_by_test_id("data-create").click()
        expect(page.get_by_text("Data/TestData/people.json")).to_be_visible()
        page.get_by_test_id("data-add-column").click()
        page.get_by_label("Key", exact=True).fill("Name")
        page.get_by_test_id("data-add-set").click()  # redraws the grid with the new key name in the labels
        page.get_by_label("Name of default").fill("Ann")
        page.get_by_label("Name of set2").fill("Bob")
        page.get_by_label("Set name").nth(1).fill("bob")
        expect(page.get_by_text("unsaved")).to_be_visible()
        remove, grid = page.get_by_role("button", name="Remove set set2").bounding_box(), \
            page.get_by_test_id("data-grid").bounding_box()
        assert remove["x"] + remove["width"] <= grid["x"] + grid["width"]  # the row's buttons are not cut off
        page.get_by_test_id("data-save").click()
        expect(page.get_by_text("unsaved")).to_be_hidden()

        saved = json.loads((workspace / "Data" / "TestData" / "people.json").read_text(encoding="utf-8"))
        assert saved == {"description": "", "defaults": {}, "sets": {"default": {"Name": "Ann"},
                                                                     "bob": {"Name": "Bob"}}}

        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text("TC-SMOKE-001").first.click()
        page.get_by_test_id("tc-dataset").click()
        page.get_by_role("option", name="people").click()
        page.get_by_role("button", name="Save").click()
        expect(page.get_by_text("unsaved")).to_be_hidden()
        browser.close()
    cases = json.loads((workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "SmokeTests.json")
                       .read_text(encoding="utf-8"))["testCases"]
    assert cases[0]["dataSet"] == "people" and cases[0]["testCaseID"] == "TC-SMOKE-001-Search"


def test_export_csv_and_import_csv_and_excel(workspace, tmp_path):
    import zipfile

    csv_file = tmp_path / "people.csv"
    csv_file.write_text("set,City,Zip\r\naustin,Austin,78701\r\ndallas,\"Dallas, TX\",75201\r\n", encoding="utf-8")
    xlsx_file = tmp_path / "people.xlsx"
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(xlsx_file, "w") as z:
        def cell(ref, value):
            return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'

        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>'
                   f'<row r="1">{cell("A1", "set")}{cell("B1", "City")}</row>'
                   f'<row r="2">{cell("A2", "houston")}{cell("B2", "Houston")}</row>'
                   '</sheetData></worksheet>')
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_test_id("tab-data").click()
        page.get_by_test_id("data-new").click()
        page.get_by_test_id("data-new-name").fill("people")
        page.get_by_test_id("data-create").click()
        expect(page.get_by_test_id("data-grid")).to_be_visible()

        page.get_by_test_id("data-import").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_test_id("data-import-file").locator("input[type=file]").set_input_files(str(csv_file))
        expect(dialog.get_by_test_id("data-import-summary")).to_contain_text("2 sets, 2 keys")
        dialog.get_by_test_id("data-import-ok").click()
        expect(page.get_by_label("City of dallas")).to_have_value("Dallas, TX")
        expect(page.get_by_text("unsaved")).to_be_visible()  # nothing is written until Save

        with page.expect_download() as download:
            page.get_by_test_id("data-export").click()
        exported = download.value
        assert exported.suggested_filename == "people.csv"
        body = Path(exported.path()).read_text(encoding="utf-8-sig")
        assert body.splitlines()[0] == "set,City,Zip" and '"Dallas, TX"' in body

        page.get_by_test_id("data-import").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_test_id("data-import-file").locator("input[type=file]").set_input_files(str(xlsx_file))
        expect(dialog.get_by_test_id("data-import-summary")).to_contain_text("1 sets, 1 keys")
        dialog.get_by_test_id("data-import-ok").click()
        expect(page.get_by_label("City of houston")).to_have_value("Houston")

        page.get_by_test_id("data-import").click()
        dialog = page.get_by_role("dialog")
        bad = tmp_path / "bad.csv"
        bad.write_text("set,bad key\r\na,1\r\n", encoding="utf-8")
        dialog.get_by_test_id("data-import-file").locator("input[type=file]").set_input_files(str(bad))
        expect(dialog.get_by_test_id("data-import-summary")).to_contain_text("Column header")
        expect(dialog.get_by_test_id("data-import-ok")).to_be_disabled()
        browser.close()


def test_history_lists_versions_shows_the_diff_and_restores_one(workspace, tmp_path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()

        # a page: change a locator, save, then bring the old one back from History
        expect(page.get_by_test_id("page-history")).to_be_visible()  # the first page opens by itself
        page.get_by_test_id("page-history").click()
        expect(page.get_by_test_id("history-empty")).to_be_visible()  # nothing saved yet
        page.keyboard.press("Escape")
        page.get_by_label("Locator", exact=True).first.fill("#changed")
        page.get_by_role("button", name="Save").click()
        expect(page.get_by_text("Saved SmokeHome")).to_be_visible()
        page.get_by_test_id("page-history").click()
        diff = page.get_by_test_id("history-diff")
        expect(diff).to_contain_text("#changed")
        expect(diff.locator(".is-del", has_text='"#q"').first).to_be_visible()  # saving also re-indents the file
        page.get_by_test_id("history-restore").click()
        expect(page.get_by_text("Restored the version")).to_be_visible()
        expect(page.get_by_label("Locator", exact=True).first).to_have_value("#q")
        browser.close()
    saved = json.loads((workspace / "Pages" / "SMOKE" / "SmokeHome.json").read_text(encoding="utf-8"))
    assert saved["fields"]["searchBox"]["locator"] == "#q"
    kept = list((workspace / ".history" / "Pages" / "SMOKE" / "SmokeHome.json").glob("*.json"))
    assert len(kept) == 2  # the original and the "#changed" version that the restore replaced
