"""The editor's plain-word actions map onto formData values and back without changing the files."""
import json

import pytest

from webapp.ui.step_actions import (
    Row, action_options, apply_action, default_action, has_value_slot, json_value, loop_of, row_action,
    set_json_value, split_key, with_loop,
)

BUTTON = {"type": "button", "locator": "#go"}
CHOICE = {"type": "button", "locator": "[data-id='Q'] button:text-is('{value}')"}
SELECT = {"type": "select", "locator": "#s"}
TEXT = {"type": "text", "locator": "#t"}


@pytest.mark.parametrize("row, cfg, action", [
    (Row("a", "text", "click"), BUTTON, "click"),
    (Row("a", "text", "Click"), BUTTON, "click"),  # the engine ignores case
    (Row("a", "text", "dblclick"), None, "dblclick"),
    (Row("a", "auto", ""), BUTTON, "generate"),
    (Row("a", "text", "hello"), TEXT, "fill"),
    (Row("a", "text", "Acme"), SELECT, "select"),
    (Row("a", "text", "No"), CHOICE, "choose"),
    (Row("a", "number", "5"), TEXT, "fill"),
    (Row("a", "json", '{"type": "confirmtext"}'), TEXT, "verify"),
    (Row("a", "json", '{"type": "keyboard", "value": "Enter"}'), TEXT, "key"),
    (Row("a", "json", '{"type": "wait", "value": "2"}'), None, "wait"),
    (Row("a", "json", '{"type": "upload", "x": 1}'), None, "custom"),
    (Row("a", "json", "not json"), None, "custom"),
])
def test_values_read_as_plain_actions(row, cfg, action):
    assert row_action(row, cfg) == action


def test_default_action_comes_from_the_field():
    assert default_action(BUTTON) == "click" and default_action(TEXT) == "fill"
    assert default_action(SELECT) == "select" and default_action(CHOICE) == "choose"
    assert default_action(None) == "fill"


def test_menu_shows_only_the_fill_like_action_that_fits():
    assert "choose" in action_options(CHOICE) and "fill" not in action_options(CHOICE)
    assert "select" in action_options(SELECT) and "choose" not in action_options(SELECT)
    assert {"fill", "select", "choose"} <= set(action_options(None))


def test_switching_actions_writes_the_same_json_the_engine_reads():
    row = Row("a", "text", "hello")
    for action, kind, raw in [("click", "text", "click"), ("generate", "auto", ""),
                              ("verify", "json", '{"type": "confirmtext"}')]:
        apply_action(row, action, TEXT)
        assert (row.kind, row.raw) == (kind, raw) and row_action(row, TEXT) == action
    apply_action(row, "key", TEXT)
    set_json_value(row, "Tab")
    assert json.loads(row.raw) == {"type": "keyboard", "value": "Tab"} and json_value(row) == "Tab"


def test_a_typed_value_survives_fill_choose_and_numbers_stay_numbers():
    row = Row("a", "text", "No")
    apply_action(row, "choose", CHOICE)
    assert (row.kind, row.raw) == ("text", "No")
    num = Row("a", "number", "7")
    apply_action(num, "fill", TEXT)
    assert (num.kind, num.raw) == ("number", "7")
    apply_action(row, "click", CHOICE)
    apply_action(row, "fill", TEXT)
    assert row.raw == ""  # "click" is an action, not a value to keep


def test_custom_keeps_existing_json():
    row = Row("a", "json", '{"type": "upload", "value": "x"}')
    apply_action(row, "custom", None)
    assert json.loads(row.raw)["type"] == "upload"


def test_keys_and_repeats():
    assert split_key("next||") == ("next", "||") and split_key("plain") == ("plain", "")
    assert loop_of("add|ELoop(3)") == ("ELoop", "3") and loop_of("add|LLoop(DICT.n)") == ("LLoop", "DICT.n")
    assert loop_of("add|") == ("", "")
    assert with_loop("add", "ELoop", "3") == "add|ELoop(3)" and with_loop("add|ELoop(3)", "", "") == "add"
    assert with_loop("add|", "LLoop", "2") == "add|LLoop(2)"


def test_value_slot_detection():
    assert has_value_slot(CHOICE) and not has_value_slot(BUTTON) and not has_value_slot(None)
