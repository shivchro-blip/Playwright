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
