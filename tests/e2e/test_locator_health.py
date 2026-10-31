"""Batch locator check: every field of a page resolved against the live page in one visit."""
from __future__ import annotations

from webapp.services.locator_health import (
    AMBIGUOUS, ERROR, HIDDEN, MISSING, OK, OUT_OF_RANGE, SKIPPED, check_fields,
)

FIELDS = {
    "begin": {"locator": "#begin", "type": "text"},
    "choose": {"role": "button", "name": "Choose date", "type": "button"},
    "firstMember": {"locator": "input.member", "index": "0", "type": "text"},
    "members": {"locator": "input.member", "type": "text"},
    "farMember": {"locator": "input.member", "index": 5, "type": "text"},
    "popup": {"locator": "#popper", "type": "text"},
    "gone": {"locator": "#does-not-exist", "type": "text"},
    "answer": {"locator": "[data-attribute-id='Taxed'] button:text-is('{value}')", "type": "button"},
    "fromEnv": {"locator": "#ENV.X", "type": "text"},
    "nothing": {"type": "text"},
    "bad": {"locator": "div[", "type": "text"},
}


def test_every_status_is_reported_from_one_visit(site_url: str):
    health = check_fields(f"{site_url}/forms.html", FIELDS)
    assert health.ok, health.error
    status = {f.name: f.status for f in health.fields}
    assert status == {
        "begin": OK, "choose": OK, "firstMember": OK, "members": AMBIGUOUS, "farMember": OUT_OF_RANGE,
        "popup": HIDDEN, "gone": MISSING, "answer": SKIPPED, "fromEnv": SKIPPED, "nothing": SKIPPED, "bad": ERROR,
    }, status
    by_name = {f.name: f for f in health.fields}
    assert by_name["members"].count == 2 and "index" in by_name["members"].detail
    assert "{value}" in by_name["answer"].detail
    assert health.counts()[OK] == 3 and health.title == "AutomateNY authoring page"


def test_bad_input_is_reported(site_url: str):
    assert "http" in check_fields("nope", FIELDS).error
    assert not check_fields("http://127.0.0.1:1/", FIELDS).ok


def test_dialog_lists_problems_first_and_can_show_everything(workspace, tmp_path, site_url: str):
    import json

    from playwright.sync_api import expect, sync_playwright

    from tests.e2e.test_ui import running_app

    (workspace / "Pages" / "SMOKE" / "Health.json").write_text(json.dumps({
        "fullpath": f"{site_url}/forms.html",
        "fields": {"begin": {"locator": "#begin", "type": "text"},
                   "gone": {"locator": "#does-not-exist", "type": "text"},
                   "members": {"locator": "input.member", "type": "text"}}}), encoding="utf-8")
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Pages").click()
        expect(page.get_by_role("heading", name="Health", level=2)).to_be_visible()  # the first page opens itself
        page.get_by_test_id("page-maintain").click()
        page.get_by_text("Check all locators on the live page...").click()
        dialog = page.get_by_role("dialog", name="Check all locators")
        expect(dialog.get_by_test_id("health-url")).to_have_value(f"{site_url}/forms.html")
        dialog.get_by_test_id("health-start").click()
        summary = dialog.get_by_test_id("health-summary")
        expect(summary).to_contain_text("1 found", timeout=60_000)
        expect(summary).to_contain_text("1 not found")
        expect(summary).to_contain_text("1 several matches")
        results = dialog.get_by_test_id("health-results")
        expect(results).to_contain_text("gone")
        expect(results).not_to_contain_text("begin")  # problems only by default
        expect(results.locator(".any-scan-row").first).to_contain_text("not found")  # worst first
        dialog.get_by_test_id("health-filter").click()
        expect(results).to_contain_text("begin")
        browser.close()
