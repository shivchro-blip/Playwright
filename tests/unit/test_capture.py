"""Smart capture: which locator is kept, how fields are named, events -> fields and steps; codegen keeps exact."""
from __future__ import annotations

from webapp.services.capture import DYNAMIC_ID, choose_locator, events_to_recording, name_for
from webapp.services.recorder import RecordOptions, parse_codegen


def cand(kind, sel, count=1, stable=True):
    return {"kind": kind, "sel": sel, "count": count, "stable": stable}


def test_generated_ids_are_recognised():
    for generated in ("input-8231", "mat-input-3", ":r1:", "ember123", "j_idt45", "a1b2c3d4e5f6", "field_12",
                      "react-select-2-input", "9start"):
        assert DYNAMIC_ID.search(generated), generated
    for fixed in ("email", "firstName", "submit-button", "zip-code", "applicantZipCode"):
        assert not DYNAMIC_ID.search(fixed), fixed


def test_the_most_stable_unique_locator_wins():
    el = {"candidates": [cand("id", '[id="input-8231"]', stable=False), cand("name", 'input[name="zip"]'),
                         cand("testid", '[data-testid="zip"]', count=2), cand("label", 'internal:label="Zip"s')]}
    assert choose_locator(el).config == {"locator": 'input[name="zip"]'}  # test id is not unique, the id generated
    assert choose_locator({"candidates": [cand("label", "L"), cand("site_id", "S")]}).config == {"locator": "S"}


def test_position_and_index_are_the_last_resort():
    anchored = choose_locator({"candidates": [cand("text", "T", count=2)],
                               "css": {"sel": "#billing > button", "count": 1, "index": 0, "anchored": True}})
    assert anchored.config == {"locator": "#billing > button"} and anchored.stable
    loose = choose_locator({"candidates": [cand("id", '[id="x-123"]', stable=False)],
                            "css": {"sel": "div > span:nth-of-type(2)", "count": 1, "index": 0}})
    assert loose.config == {"locator": "div > span:nth-of-type(2)"} and not loose.stable  # beats a generated id
    several = choose_locator({"candidates": [cand("text", "T", count=3)], "css": {"sel": "p", "count": 3, "index": 2}})
    assert several.config == {"locator": "T", "index": 2} and "3 elements" in several.note


def test_names_come_from_what_a_person_sees():
    opts = RecordOptions()
    assert name_for({"label": "First name", "id": "input-8231"}, "text", set(), opts) == "firstName"
    assert name_for({"placeholder": "Email address"}, "text", set(), opts) == "emailAddress"
    assert name_for({"id": "input-8231", "name": "zip"}, "text", set(), opts) == "zip"  # generated id skipped
    assert name_for({"text": "Save", "role": "button"}, "button", {"saveButton"}, opts) == "saveButton2"
    site = RecordOptions(id_attribute="data-attribute-id", id_prefix="LSI_")
    assert name_for({"site_id": "LSI_TaxNotPaid", "role": "button", "text": "No"}, "button", set(), site) == \
        "TaxNotPaidNo"
    assert name_for({"site_id": "LSI_TaxNotPaid", "site_id_own": True}, "text", set(), site) == "TaxNotPaid"


def el(name, **extra):
    return {"tag": "input", "role": "textbox", "name": name, "candidates": [cand("name", f'input[name="{name}"]')],
            **extra}


def button(text):
    return {"tag": "button", "role": "button", "text": text,
            "candidates": [cand("text", f'internal:role=button[name="{text}"s]')]}


def test_events_become_fields_and_one_step():
    events = [{"action": "fill", "el": el("city"), "value": "Albany"},
              {"action": "fill", "el": el("city"), "value": "Albany NY"},  # sent twice: the last value is kept
              {"action": "fill", "el": el("pass", type="password"), "value": "", "secret": True},
              {"action": "click", "el": button("Next")}, {"action": "click", "el": button("Next")},
              {"action": "press", "el": el("city"), "value": "Enter"},
              {"action": "select", "el": {**el("state"), "tag": "select", "role": "combobox",
                                          "candidates": [cand("name", 'select[name="state"]')]}, "value": "Texas"},
              {"action": "noise", "el": {}}]
    rec = events_to_recording(events, "http://x/apply")
    assert rec.steps == [{"city": "Albany NY", "pass": "ENV.PASS", "nextButton": "click", "nextButton|": "click",
                          "city|": {"type": "keyboard", "value": "Enter"}, "stateSelect": "Texas"}]
    assert rec.fields["stateSelect"] == {"locator": 'select[name="state"]', "type": "select"}
    assert rec.profiles[0]["city"]["name"] == "city"
    assert any("credential" in w for w in rec.warnings) and rec.actions == 6


def test_user_names_are_kept_and_passwords_come_from_env():
    events = [{"action": "fill", "el": el("loginform:username", label="Username"), "value": "dummy.user"},
              {"action": "fill", "el": el("loginform:password", type="password", label="Password"), "value": "",
               "secret": True},
              {"action": "fill", "el": el("loginform:password", type="password", label="Password"), "value": "",
               "secret": True}]
    rec = events_to_recording(events, "http://x/login")
    assert rec.steps == [{"username": "dummy.user", "password": "ENV.PASSWORD"}]
    assert len(rec.warnings) == 1 and "Add PASSWORD=<value> to .env" in rec.warnings[0]


def test_wizard_mode_splits_on_the_buttons_that_move_on():
    events = [{"action": "fill", "el": el("a"), "value": "1"}, {"action": "click", "el": button("Continue")},
              {"action": "fill", "el": el("b"), "value": "2"}, {"action": "click", "el": button("Submit")}]
    rec = events_to_recording(events, "http://x", RecordOptions(break_on=("Continue", "Submit")))
    assert [list(s.fields) for s in rec.screens] == [["a", "continueButton"], ["b", "submitButton"]]
    assert len(rec.profiles) == 2


def test_codegen_exact_matches_stay_exact():
    source = """
def run(page):
    page.goto("http://x/")
    page.get_by_role("textbox", name="Name", exact=True).fill("Ann")
    page.get_by_label("Last name", exact=True).fill("Lee")
    page.get_by_role("button", name="Save").click()
"""
    rec = parse_codegen(source)
    cfgs = list(rec.fields.values())
    assert cfgs[0]["locator"] == 'internal:role=textbox[name="Name"s]'
    assert cfgs[1]["locator"] == 'internal:label="Last name"s'
    assert cfgs[2] == {"role": "button", "name": "Save", "type": "button"}  # not exact in the recording: unchanged
