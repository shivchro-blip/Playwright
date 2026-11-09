"""Plain-language steps: lines read as intents and matched only against real fields."""
from __future__ import annotations

import pytest

from webapp.services.intent import compile_lines, parse, words

PAGES = {
    "LoginPage": {"userName": {"locator": "#user", "type": "text"},
                  "password": {"locator": "#pw", "type": "text"},
                  "signIn": {"role": "button", "name": "Sign in", "type": "button"}},
    "ApplyForm": {"firstName": {"label": "First name", "type": "text"},
                  "lastName": {"label": "Last name", "type": "text"},
                  "state": {"locator": "select[name='state']", "type": "select"},
                  "agree": {"label": "I agree to the terms", "type": "checkbox"},
                  "submit": {"role": "button", "name": "Submit application", "type": "button"},
                  "search": {"placeholder": "Search", "type": "text"}},
}


@pytest.mark.parametrize("line, action, target, value", [
    ('Type "Ann" into first name', "fill", "first name", "Ann"),
    ("1. fill Last name with 'Lee'", "fill", "Last name", "Lee"),
    ('Choose "Texas" from state', "select", "state", "Texas"),
    ("- Tick I agree", "check", "I agree", ""),
    ("Click the Submit button.", "click", "the Submit button", ""),
    ("Press Enter", "press", "", "Enter"),
    ("Look around", "", "", ""),
])
def test_parse(line, action, target, value):
    i = parse(line)
    assert (i.action, i.target, i.value) == (action, target, value)


def test_words_split_names_and_drop_filler():
    assert words("firstName") == ["first", "name"] and words("the Submit button") == ["submit"]


def test_a_plain_test_compiles_to_explicit_steps_on_real_fields():
    text = """Open the login page
    Type "ann" into user name
    Enter "ENV.PASSWORD" in password
    Click Sign in
    Type "Ann" into first name
    fill last name with "Lee"
    Choose "Texas" from state
    Tick I agree to the terms
    Click Submit application
    Type "shoes" into search
    Press Enter"""
    c = compile_lines(text, PAGES)
    assert c.ok, [r.to_dict() for r in c.rows if r.status != "resolved"]
    assert c.steps == [
        {"screen": "LoginPage", "do": [{"field": "userName", "value": "ann"},
                                       {"field": "password", "value": "ENV.PASSWORD"},
                                       {"field": "signIn", "click": True}]},
        {"screen": "ApplyForm", "do": [{"field": "firstName", "value": "Ann"}, {"field": "lastName", "value": "Lee"},
                                       {"field": "state", "value": "Texas"}, {"field": "agree", "check": True},
                                       {"field": "submit", "click": True}, {"field": "search", "value": "shoes"},
                                       {"field": "search", "press": "Enter"}]}]
    assert c.rows[0].status == "page" and c.rows[0].page == "LoginPage"


def test_what_does_not_match_is_said_never_guessed():
    c = compile_lines('Type "x" into favourite colour\nClick name\nDo a little dance\nOpen the moon page\nPress Tab',
                      PAGES)
    status = [(r.status, r.note) for r in c.rows]
    assert status[0][0] == "unresolved" and "favourite colour" in status[0][1]
    assert status[1][0] == "ambiguous" and len(c.rows[1].candidates) >= 2  # first name, last name, user name
    assert status[2] == ("unresolved", "Not a step the rules can read.")
    assert status[3][0] == "unresolved" and "moon" in status[3][1]
    assert status[4][0] == "unresolved" and "which field" in status[4][1]
    assert c.steps == [] and not c.ok


def test_ai_fills_gaps_but_only_with_real_fields():
    asked = []

    def ai(lines, pages):
        asked.append(lines)
        return {"Look for shoes": {"page": "ApplyForm", "field": "search", "action": "fill", "value": "shoes"},
                "Make up a field": {"page": "ApplyForm", "field": "noSuchField", "action": "click"}}

    c = compile_lines('Open apply form\nLook for shoes\nMake up a field\nTick I agree', PAGES, resolve_rest=ai)
    assert asked == [["Look for shoes", "Make up a field"]]  # only what the rules left
    assert c.rows[1].status == "resolved" and c.rows[1].source == "ai" and c.rows[1].value == "shoes"
    assert c.rows[2].status == "unresolved" and "does not exist: ignored" in c.rows[2].note
    assert c.steps == [{"screen": "ApplyForm", "do": [{"field": "search", "value": "shoes"},
                                                      {"field": "agree", "check": True}]}]
