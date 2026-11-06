"""Smart capture: a recording of a page whose ids change on every load still runs the next time."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from tests.e2e.test_runs import run_and_wait
from webapp.services.pom_service import PomService
from webapp.services.recorder import Recorder, save_recording
from webapp.services.test_case_service import TestCaseService

DRIVE = Path(__file__).resolve().parent / "fixtures" / "recorder" / "drive_application.py"


def record(url: str, monkeypatch, mode: str = "capture"):
    monkeypatch.setenv("AUTOPLAY_RECORDER_HEADLESS", "true")
    monkeypatch.setenv("AUTOPLAY_RECORDER_DRIVE", str(DRIVE))
    recorder = Recorder(sys.executable)
    session = recorder.start(url, mode=mode)
    deadline = time.monotonic() + 120
    while session.status in ("starting", "recording") and time.monotonic() < deadline:
        time.sleep(0.3)
    assert session.status == "finished", session.error
    return session.recording


def test_recorded_fields_have_real_names_and_stable_locators_and_the_test_replays(
        workspace: Path, tmp_path: Path, site_url: str, monkeypatch):
    rec = record(f"{site_url}/recording.html", monkeypatch)
    fields = rec.fields
    locators = {name: cfg["locator"] for name, cfg in fields.items()}
    assert locators == {
        "firstName": 'input[name="firstName"]',
        "lastName": 'internal:label="Last name"s',
        "name": 'internal:label="Name"s',  # exact: "Name" alone also matches First name and Last name
        "emailAddress": 'input[placeholder="Email address"]',
        "phoneNumber": '[data-testid="phone-number"]',
        "stateSelect": 'select[name="state"]',
        "iAgreeToTheTermsCheckbox": 'input[name="agree"][value="yes"]',
        "contactMeByPhoneRadio": 'input[name="contact"][value="phone"]',
        "password": 'internal:label="Password"s',
        "saveButton": "#billing > button",  # two "Save" buttons: located inside its section
        "continueButton": 'internal:role=button[name="Continue"s]',
        "finishButton": 'internal:role=button[name="Finish"s]',
    }
    assert not any("input-" in loc for loc in locators.values())  # the generated ids are never used
    assert [fields[n]["type"] for n in ("firstName", "stateSelect", "iAgreeToTheTermsCheckbox",
                                        "contactMeByPhoneRadio", "saveButton")] == \
        ["text", "select", "checkbox", "radio", "button"]
    step = rec.steps[0]
    assert step == {"firstName": "Ann", "lastName": "Lee", "name": "Lee Bakery", "emailAddress": "ann@example.com",
                    "phoneNumber": "5185550100", "stateSelect": "Texas",
                    "iAgreeToTheTermsCheckbox": "check", "contactMeByPhoneRadio": "check", "password": "ENV.PASSWORD",
                    "saveButton": "click", "continueButton": "click", "finishButton": "click"}
    assert any("credential" in w for w in rec.warnings) and "hunter2" not in rec.source
    assert not any("may not find" in w for w in rec.warnings)

    pom = PomService(workspace, workspace.parent)
    tests = TestCaseService(workspace, pom)
    save_recording(pom, tests, "SMOKE", rec, page_name="ApplicationPage", base_url=site_url,
                   test_id="TC-REC-001", test_file="Recorded.json", tags=["recorded"])
    page = json.loads((workspace / "Pages" / "SMOKE" / "ApplicationPage.json").read_text(encoding="utf-8"))
    assert page["path"] == "/recording.html"
    profiles = json.loads((workspace / "Data" / "locatorProfiles" / "SMOKE.json").read_text(encoding="utf-8"))
    assert profiles["ApplicationPage"]["firstName"]["name"] == "firstName"  # self-healing knows it from day one
    assert profiles["ApplicationPage"]["lastName"]["label"] == "Last name"

    # the next run loads the page again: every id is new, and the recorded test still passes
    run = run_and_wait(workspace, tmp_path, tags=["recorded"])
    assert run.status == "passed", "\n".join(run.log[-60:])
    assert {r.test_id: r.outcome for r in run.results.values()} == {"TC-REC-001": "passed"}


def test_record_dialog_uses_smart_capture_by_default(workspace: Path, tmp_path: Path, site_url: str):
    from playwright.sync_api import expect, sync_playwright

    from tests.e2e.test_ui import running_app
    with running_app(tmp_path, AUTOPLAY_RECORDER_HEADLESS="true", AUTOPLAY_RECORDER_DRIVE=str(DRIVE)) as url, \
            sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_test_id("recorder-switch").click()
        page.get_by_test_id("record-button").click()
        dialog = page.get_by_role("dialog", name="Record a test")
        expect(dialog.get_by_test_id("rec-mode")).to_contain_text("Smart capture")
        dialog.get_by_label("Start URL").fill(f"{site_url}/recording.html")
        dialog.get_by_test_id("rec-start").click()
        expect(dialog.get_by_text("12 fields")).to_be_visible(timeout=60_000)
        expect(dialog.get_by_text('internal:label="Last name"s')).to_be_visible()
        expect(dialog.get_by_text("looks like a credential")).to_be_visible()
        dialog.get_by_label("Test case ID").fill("TC-REC-UI")
        dialog.get_by_test_id("rec-save").click()
        expect(dialog).to_be_hidden()
        browser.close()
    saved = json.loads((workspace / "Data" / "locatorProfiles" / "SMOKE.json").read_text(encoding="utf-8"))
    assert any("firstName" in fields for fields in saved.values())
