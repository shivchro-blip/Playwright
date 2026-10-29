"""Recorder: codegen output -> page fields + steps, merging, saving, the on/off setting and the session."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from webapp.services.env_config import FIELDS, RUN_OPTION_KEYS, EnvConfigService
from webapp.services.pom_service import PomService
from webapp.services.recorder import (
    Recorder, RecorderError, field_config, merge_fields, parse_codegen, save_recording, suggest_page_name,
)
from webapp.services.test_case_service import TestCaseService as CaseService  # pytest collects Test* names

FAKE_CODEGEN = Path(__file__).resolve().parents[1] / "e2e" / "fixtures" / "recorder" / "fake_codegen.py"


def codegen(*lines: str, url: str = "https://shop.test/login") -> str:
    body = "\n".join(f"    {line}" for line in lines)
    return f'''import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("{url}")
{body}

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
'''


def test_actions_become_fields_and_one_step_in_order():
    rec = parse_codegen(codegen(
        'page.get_by_label("Email").click()',
        'page.get_by_label("Email").fill("ann@example.com")',
        'page.get_by_role("button", name="Sign in", exact=True).click()',
        'page.get_by_role("link", name="Red shoes").first.click()',
        'page.get_by_test_id("size").select_option("42")',
        'page.get_by_role("checkbox", name="Gift wrap").check()',
        'page.get_by_placeholder("Search").fill("boots")',
        'page.keyboard.press("Enter")',
        'page.get_by_role("button", name="Sign in", exact=True).click()',
    ))
    assert rec.start_url == "https://shop.test/login" and rec.actions == 8
    assert rec.fields == {
        "email": {"label": "Email", "type": "text"},
        "signInButton": {"role": "button", "name": "Sign in", "type": "button"},
        "redShoesLink": {"role": "link", "name": "Red shoes", "index": 0, "type": "link"},
        "sizeSelect": {"locator": '[data-testid="size"]', "type": "select"},
        "giftWrapCheckbox": {"role": "checkbox", "name": "Gift wrap", "type": "checkbox"},
        "search": {"placeholder": "Search", "type": "text"},
    }
    # one step (a second would navigate to the page again); a reused field gets a pipe suffix
    assert rec.steps == [{"email": "ann@example.com", "signInButton": "click", "redShoesLink": "click",
                          "sizeSelect": "42", "giftWrapCheckbox": "check", "search": "boots",
                          "search|": {"type": "keyboard", "value": "Enter"}, "signInButton|": "click"}]
    assert rec.page("https://shop.test") == {"path": "/login", "fields": rec.fields}
    assert rec.page("https://other.test")["fullpath"] == "https://shop.test/login"


def test_credentials_are_not_saved_and_are_masked_in_the_code():
    rec = parse_codegen(codegen('page.get_by_label("Password").fill("hunter2")',
                                'page.locator("#otp-code").fill("123456")'))
    assert rec.steps == [{"password": "", "otpCode": ""}]
    assert "hunter2" not in rec.source and "123456" not in rec.source
    assert sum("looks like a credential" in w for w in rec.warnings) == 2


def test_popups_chains_iframes_assertions_and_extra_navigation():
    rec = parse_codegen(codegen(
        'with page.expect_popup() as page1_info:',
        '    page.get_by_role("link", name="Help").click()',
        'page1 = page1_info.value',
        'page1.get_by_placeholder("Search help").fill("returns")',
        'page.locator("li").filter(has_text="Express").get_by_role("radio").check()',
        'page.frame_locator("#pay").get_by_label("Card").fill("4242")',
        'expect(page.get_by_role("heading")).to_contain_text("Basket")',
        'page.goto("https://shop.test/checkout")',
        'page.get_by_role("button").dblclick()',
    ))
    assert rec.fields["helpLink"]["waitForNewTab"] is True
    assert rec.fields["searchHelp"] == {"placeholder": "Search help", "type": "text"}
    chained = next(c for c in rec.fields.values() if "internal:" in str(c.get("locator")))
    assert chained["locator"] == 'li >> internal:has-text="Express"i >> internal:role=radio'
    assert rec.assertions == 1 and rec.steps[0]["button"] == "dblclick"
    text = " ".join(rec.warnings)
    assert "iframe" in text and "navigated to https://shop.test/checkout" in text and "assertion" in text


def test_field_config_nth_and_unreadable_code():
    import ast
    call = ast.parse('page.get_by_role("row", name="Ann").nth(2)').body[0].value
    from webapp.services.recorder import _chain
    _, parts = _chain(call)
    assert field_config(parts, []) == {"role": "row", "name": "Ann", "index": 2}
    with pytest.raises(RecorderError):
        parse_codegen("def run(:\n")
    assert "No actions were recorded." in parse_codegen(codegen()).warnings


def test_merge_reuses_matching_fields_and_renames_clashes():
    existing = {"searchBox": {"locator": "#q", "type": "text"}, "notes": {"locator": "#other", "type": "text"}}
    recorded = {"q": {"locator": "#q", "type": "text"}, "notes": {"label": "Notes", "type": "text"}}
    fields, renames = merge_fields(existing, recorded)
    assert renames == {"q": "searchBox", "notes": "notes2"}
    assert fields["notes2"] == {"label": "Notes", "type": "text"} and len(fields) == 3


def test_suggest_page_name():
    assert suggest_page_name("https://x.test/app/check-out.html") == "CheckOutPage"
    assert suggest_page_name("https://x.test/") == "HomePage"
    assert suggest_page_name("https://x.test/404") == "Page404Page"


def test_save_recording_creates_or_merges_the_page_and_appends_a_test(tmp_path):
    (tmp_path / "Pages" / "SYS").mkdir(parents=True)
    (tmp_path / "Pages" / "SYS" / "Home.json").write_text(json.dumps(
        {"path": "/", "fields": {"searchBox": {"locator": "#q", "type": "text"}}}), encoding="utf-8")
    pom = PomService(tmp_path, tmp_path)
    tests = CaseService(tmp_path, pom)
    rec = parse_codegen(codegen('page.locator("#q").fill("x")', 'page.get_by_label("Notes").fill("y")',
                                url="https://site.test/"))
    merged = save_recording(pom, tests, "SYS", rec, page_name="Home", base_url="https://site.test",
                            test_id="TC-1", test_file="Rec.json", env="dev", tags=["SYS", "recorded"])
    assert not merged.page_created and merged.renames["q"] == "searchBox"
    page, _ = pom.load("SYS", "Home.json")
    assert set(page["fields"]) == {"searchBox", "notes"} and page["path"] == "/"
    created = save_recording(pom, tests, "SYS", rec, page_name="NewPage", base_url="https://site.test",
                             test_id="TC-2", test_file="Rec.json")
    assert created.page_created and created.test_index == 1
    data, _ = tests.load_file("SYS", "Rec.json")
    assert data["testCases"][0]["steps"] == [{"screen": "Home", "formData": {"searchBox": "x", "notes": "y"}}]
    assert data["testCases"][1]["steps"][0]["screen"] == "NewPage"
    with pytest.raises(RecorderError):
        save_recording(pom, tests, "SYS", rec, page_name="Other", base_url="", test_id=" ", test_file="Rec.json")


def test_setting_is_off_by_default_and_never_a_run_option(tmp_path):
    field = next(f for f in FIELDS if f.key == "recorderEnabled")
    assert field.default == "false" and "recorderEnabled" not in RUN_OPTION_KEYS
    env = EnvConfigService(tmp_path / ".env")
    assert env.load()["recorderEnabled"] == "false"
    env.save({"recorderEnabled": True})
    assert env.load()["recorderEnabled"] == "true"


def wait_done(recorder: Recorder, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while recorder.active and time.monotonic() < deadline:
        time.sleep(0.1)
    return recorder.session


def test_session_runs_codegen_and_parses_its_output():
    recorder = Recorder(command=[sys.executable, str(FAKE_CODEGEN)])
    with pytest.raises(RecorderError):
        recorder.start("file:///etc/passwd")
    session = recorder.start("http://127.0.0.1:1/index.html")
    workdir = session.workdir
    session = wait_done(recorder)
    assert session.status == "finished", session.error
    assert set(session.recording.fields) == {"q", "notes", "phone"}
    assert not workdir.exists()  # the raw recording (which may hold passwords) is deleted


def test_session_reports_a_browser_closed_without_recording(tmp_path):
    script = tmp_path / "noop.py"
    script.write_text("print('no display')\n", encoding="utf-8")
    recorder = Recorder(command=[sys.executable, str(script)])
    recorder.start("http://127.0.0.1:1/")
    session = wait_done(recorder)
    assert session.status == "failed" and "without a recording" in session.error and "no display" in session.error


def test_cancel_ends_the_codegen_process_and_discards_the_recording(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    recorder = Recorder(command=[sys.executable, str(script)])
    session = recorder.start("http://127.0.0.1:1/")
    assert recorder.active
    with pytest.raises(RecorderError):  # one recording at a time
        recorder.start("http://127.0.0.1:1/")
    recorder.cancel()
    assert session.proc.wait(timeout=10) is not None
    deadline = time.monotonic() + 10
    while session.workdir.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert session.status == "cancelled" and session.recording is None and not session.workdir.exists()
