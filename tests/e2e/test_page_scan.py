"""Add fields from a live page: scan the local wizard and forms pages, and check every proposed locator."""
from __future__ import annotations

from webapp.services.locator_check import check_locator
from webapp.services.page_scan import scan_page


def test_scan_names_and_locates_the_visible_elements(site_url: str):
    scan = scan_page(f"{site_url}/wizard.html", id_attribute="data-attribute-id", id_prefix="LSI_DEMO_")
    assert scan.ok, scan.error
    by_name = {c.name: c for c in scan.candidates}
    # only the first screen is visible: the later screens' fields are not offered
    assert set(by_name) == {"BusName", "HomeBasedYes", "HomeBasedNo", "saveContinueButton"}, list(by_name)
    assert by_name["BusName"].cfg == {"locator": "[data-attribute-id='LSI_DEMO_BusName'] input"}
    assert by_name["BusName"].type == "text" and by_name["HomeBasedNo"].type == "button"
    assert by_name["HomeBasedNo"].cfg == {"locator": "[data-attribute-id='LSI_DEMO_HomeBased'] button:text-is('No')"}
    assert by_name["saveContinueButton"].cfg == {"role": "button", "name": "Save & Continue"}
    assert scan.skipped == 0 and scan.total >= 4


def test_every_proposed_locator_finds_exactly_its_element(site_url: str):
    url = f"{site_url}/forms.html"
    scan = scan_page(url, id_attribute="data-attribute-id")
    assert scan.ok, scan.error
    assert {"Taxed", "Home"} == {c.name.removesuffix("Yes").removesuffix("No") for c in scan.candidates
                                 if c.name.startswith(("Taxed", "Home"))}
    assert any(c.cfg.get("locator") == "#begin" and c.type == "text" for c in scan.candidates)
    members = [c for c in scan.candidates if c.cfg.get("locator") == "input.member"]
    assert [c.cfg.get("index") for c in members] == [0, 1] and all(c.matches == 2 for c in members)
    for candidate in scan.candidates:  # the checker resolves it the way a run does
        result = check_locator(url, candidate.cfg)
        assert result.ok and result.error == "", (candidate.name, result.error)
        assert result.count == candidate.matches, candidate.name
        assert candidate.matches == 1 or result.index is not None, candidate.name


def test_scan_reports_bad_input(site_url: str):
    assert "http" in scan_page("not a url").error
    assert "attribute" in scan_page(f"{site_url}/forms.html", id_attribute="bad attr").error


def test_scan_dialog_adds_selected_fields_to_the_page(workspace, tmp_path, site_url: str):
    import json

    from playwright.sync_api import expect, sync_playwright

    from tests.e2e.test_ui import running_app

    pages = workspace / "Pages" / "SMOKE"
    (pages / "ScanMe.json").write_text(json.dumps({"fullpath": f"{site_url}/wizard.html", "fields": {
        "BusName": {"locator": "[data-attribute-id='LSI_DEMO_BusName'] input", "type": "text"}}}), encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Pages").click()
        # the Generator opens the first page by itself: let that finish, or it re-renders under the clicks below
        expect(page.get_by_role("heading", name="ScanMe", level=2)).to_be_visible()
        page.get_by_role("region", name="Pages").locator("tbody tr", has_text="ScanMe").click()
        page.get_by_test_id("scan-open").click()
        dialog = page.get_by_role("dialog", name="Add fields from the live page")
        expect(dialog.get_by_test_id("scan-url")).to_have_value(f"{site_url}/wizard.html")
        dialog.get_by_test_id("scan-id-attr").fill("data-attribute-id")
        dialog.get_by_test_id("scan-id-prefix").fill("LSI_DEMO_")
        dialog.get_by_test_id("scan-start").click()
        results = dialog.get_by_test_id("scan-results")
        expect(results).to_contain_text("BusName", timeout=60_000)
        expect(results).to_contain_text("already on the page")  # BusName is on the page: not added twice
        dialog.get_by_test_id("scan-add").click()
        expect(dialog).to_be_hidden()
        expect(page.get_by_text("unsaved")).to_be_visible()
        page.get_by_role("button", name="Save").click()
        expect(page.get_by_text("unsaved")).to_be_hidden()
        browser.close()
    fields = json.loads((pages / "ScanMe.json").read_text(encoding="utf-8"))["fields"]
    assert list(fields) == ["BusName", "HomeBasedYes", "HomeBasedNo", "saveContinueButton"]
    assert fields["HomeBasedNo"] == {"locator": "[data-attribute-id='LSI_DEMO_HomeBased'] button:text-is('No')",
                                     "type": "button"}
    assert fields["saveContinueButton"] == {"role": "button", "name": "Save & Continue", "type": "button"}
