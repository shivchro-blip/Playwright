"""Element identity end to end: a scan records it, and a run uses it when a locator breaks."""
from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.test_runs import run_and_wait
from webapp.services.heals import read_records
from webapp.services.identity_health import collect
from webapp.services.page_scan import scan_page


def test_a_scan_records_each_fields_identity(site_url: str):
    scan = scan_page(f"{site_url}/index.html")
    assert scan.ok, scan.error
    search = next(c for c in scan.candidates if c.cfg == {"locator": "#q"})
    ident = search.identity
    assert ident["fingerprint"] == {"tag": "input", "id": "q", "name": "q", "label": "Search"}
    assert {"locator": "input[name='q']"} in ident["locators"] and {"locator": "#q"} not in ident["locators"]
    assert ident["verified"]


def test_a_broken_locator_is_rescued_by_the_fields_identity(workspace: Path, tmp_path: Path):
    page_file = workspace / "Pages" / "SMOKE" / "SmokeHome.json"
    page = json.loads(page_file.read_text(encoding="utf-8"))
    page["fields"]["searchBox"] = {  # the id was renamed in a redesign; the field still knows the element
        "locator": "#gone", "type": "text",
        "identity": {"fingerprint": {"tag": "input", "id": "q", "name": "q", "label": "Search"},
                     "locators": [{"locator": "#notes"}, {"locator": "input[name='q']"}]}}
    page_file.write_text(json.dumps(page, indent=2), encoding="utf-8")

    run = run_and_wait(workspace, tmp_path, tags=["smoke"], test_ids=["TC-SMOKE-001-Search"])
    assert run.status == "passed", "\n".join(run.log[-60:])
    records = read_records(run.results_dir, "identity")
    rescue = [r for r in records if r["field"] == "searchBox"]
    # #notes finds one element, but a textarea is not this input: the name locator is the one used
    assert rescue and rescue[0]["outcome"] == "fallback" and rescue[0]["locator"] == {"locator": "input[name='q']"}
    assert rescue[0]["used"] == 1 and rescue[0]["score"] >= 60
    health = collect([run], system="SMOKE")[("SMOKE", "SmokeHome", "searchBox")]
    assert health.score == 50 and health.fallback_cfg == {"locator": "input[name='q']"}
    assert json.loads(page_file.read_text(encoding="utf-8"))["fields"]["searchBox"]["locator"] == "#gone"  # unchanged


def test_learn_identities_then_promote_the_fallback_a_run_used(workspace: Path, tmp_path: Path):
    import time
    import urllib.request

    from playwright.sync_api import expect, sync_playwright

    from tests.e2e.test_ui import running_app

    page_file = workspace / "Pages" / "SMOKE" / "SmokeHome.json"
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))

        def open_smoke_home() -> None:
            page.goto(url, wait_until="networkidle")
            page.keyboard.press("Control+k")
            box = page.get_by_test_id("palette-input")
            expect(box).to_have_value("")
            box.fill("SmokeHome")
            expect(page.get_by_test_id("palette-results").get_by_role("option").first).to_contain_text("SmokeHome")
            box.press("Enter")
            expect(page.get_by_text("Pages/SMOKE/SmokeHome.json")).to_be_visible()

        open_smoke_home()
        expect(page.get_by_test_id("identity-searchBox")).to_have_attribute("data-tone", "none")
        page.get_by_test_id("learn-open").click()
        dialog = page.get_by_role("dialog", name="Learn identities")
        dialog.get_by_test_id("learn-scan").click()
        results = dialog.get_by_test_id("learn-results")
        expect(results.locator("[data-field='searchBox']")).to_contain_text("fallback locator", timeout=60_000)
        expect(results.locator("[data-field='missing']")).to_contain_text("not found")
        dialog.get_by_test_id("learn-apply").click()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("unsaved")).to_be_hidden()
        saved = json.loads(page_file.read_text(encoding="utf-8"))["fields"]
        assert saved["searchBox"]["identity"]["fingerprint"]["name"] == "q"
        assert "identity" not in saved["missing"]

        # a redesign renames the id: the run still passes on the identity's first fallback
        saved["searchBox"]["locator"] = "#gone"
        data = json.loads(page_file.read_text(encoding="utf-8"))
        page_file.write_text(json.dumps({**data, "fields": saved}, indent=2), encoding="utf-8")
        req = urllib.request.Request(f"{url}api/v1/runs", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"system": "SMOKE", "env": "dev", "tags": ["smoke"],
                                                      "test_ids": ["TC-SMOKE-001-Search"], "browsers": ["chromium"],
                                                      "options": {"headlessMode": True}}).encode())
        run_id = json.loads(urllib.request.urlopen(req, timeout=30).read())["id"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            run = json.loads(urllib.request.urlopen(f"{url}api/v1/runs/{run_id}", timeout=30).read())
            if run["status"] not in ("queued", "running"):
                break
            time.sleep(1)
        assert run["status"] == "passed", run

        open_smoke_home()
        button = page.get_by_test_id("identity-searchBox")
        expect(button).to_have_attribute("data-tone", "bad")  # one look, and it needed a fallback: 50%
        button.click()
        ident = page.get_by_test_id("identity-dialog")
        expect(ident.get_by_test_id("identity-health")).to_contain_text("50%")
        ident.get_by_test_id("identity-promote").click()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("unsaved")).to_be_hidden()
        field = json.loads(page_file.read_text(encoding="utf-8"))["fields"]["searchBox"]
        assert field["locator"] != "#gone" and field["identity"]["locators"][0] == {"locator": "#gone"}
        browser.close()
    assert problems == []
