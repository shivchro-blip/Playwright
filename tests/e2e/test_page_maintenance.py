"""Pages show who uses them, and rename / tidy change the page and its tests together, after a preview."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app

SAVE = {"locator": "button.save", "index": "0", "type": "button", "autoGen": "click"}


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_used_by_rename_and_tidy(workspace: Path, tmp_path: Path):
    pages = workspace / "Pages" / "SMOKE"
    tests = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Maint.json"
    write(pages / "AppForm.json", {"fields": {
        "BusName": {"locator": "#biz", "type": "text"}, "Save": dict(SAVE), "Save1": dict(SAVE),
        "Spare": {"locator": "#spare", "type": "text"}}})
    write(pages / "Lonely.json", {"fields": {"x": {"locator": "#x", "type": "text"}}})
    write(tests, {"testCases": [{"testCaseID": "TC-MAINT", "system": "SMOKE", "env": "dev", "module": "m",
                                 "tags": ["maint"], "steps": [{"screen": "AppForm", "formData": {
                                     "BusName": "Acme", "Save": "click", "Save1": "click"}}]}]})
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Pages").click()

        row = page.get_by_role("region", name="Pages").locator("tbody tr", has_text="AppForm")
        expect(row).to_contain_text("1")  # used by TC-MAINT
        expect(page.get_by_role("region", name="Pages").locator("tbody tr", has_text="Lonely")).to_contain_text("unused")
        row.click()
        used = page.get_by_test_id("page-used-by")
        expect(used).to_contain_text("Used by 1 test")
        used.click()
        expect(page.get_by_role("button", name="TC-MAINT")).to_be_visible()
        expect(page.locator(".is-unused")).to_have_count(1)  # only Spare is unused

        # rename a field: page and test change together, after a preview
        page.get_by_test_id("page-maintain").click()
        page.get_by_text("Rename a field...").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_test_id("rename-new").fill("BusinessName")
        dialog.get_by_test_id("refactor-preview").click()
        expect(dialog.get_by_text("These files will change:")).to_be_visible()
        assert "BusName" in read(pages / "AppForm.json")["fields"]  # the preview wrote nothing
        dialog.get_by_test_id("refactor-apply").click()
        expect(dialog).to_be_hidden()
        assert "BusinessName" in read(pages / "AppForm.json")["fields"]
        step = read(tests)["testCases"][0]["steps"][0]
        assert list(step["formData"]) == ["BusinessName", "Save", "Save1"]

        # tidy: merge the identical Save fields, drop the redundant autoGen and the unused field
        page.get_by_test_id("page-maintain").click()
        page.get_by_text("Tidy up this page...").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_test_id("tidy-duplicate").click()
        dialog.get_by_test_id("tidy-unused").click()
        dialog.get_by_test_id("refactor-preview").click()
        expect(dialog.get_by_text("These files will change:")).to_be_visible()
        dialog.get_by_test_id("refactor-apply").click()
        expect(dialog).to_be_hidden()
        fields = read(pages / "AppForm.json")["fields"]
        assert list(fields) == ["BusinessName", "Save"] and "autoGen" not in fields["Save"]
        step = read(tests)["testCases"][0]["steps"][0]
        assert list(step["formData"]) == ["BusinessName", "Save", "Save|"]

        # rename the page itself
        page.get_by_test_id("page-maintain").click()
        page.get_by_text("Rename page...").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_test_id("rename-new").fill("Application")
        dialog.get_by_test_id("refactor-preview").click()
        dialog.get_by_test_id("refactor-apply").click()
        expect(dialog).to_be_hidden()
        assert (pages / "Application.json").exists() and not (pages / "AppForm.json").exists()
        assert read(tests)["testCases"][0]["steps"][0]["screen"] == "Application"
        expect(page.get_by_text("Pages/SMOKE/Application.json")).to_be_visible()
        browser.close()
