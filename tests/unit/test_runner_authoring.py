"""Opt-in runner options: step-level index and calendar date values."""
from datetime import date

import pytest

from utils.date_value import months_to, parse_date_value
from utils.step_options import fields_for_step

FIELDS = {
    "member": {"locator": "input.member", "type": "text", "indexed": True},
    "memberAt2": {"locator": "input.member", "index": 2, "type": "text", "indexed": True},
    "save": {"locator": "button", "index": "0", "type": "button"},
    "raw": "not a dict",
}


def test_step_index_replaces_the_index_of_indexed_fields_only():
    out = fields_for_step(FIELDS, {"screen": "P", "index": 1, "formData": {}})
    assert out["member"]["index"] == 1 and out["memberAt2"]["index"] == 1
    assert out["save"] == FIELDS["save"] and out["raw"] == "not a dict"
    assert "index" not in FIELDS["member"]  # the page itself is not modified


@pytest.mark.parametrize("step", [{"screen": "P"}, {"screen": "P", "formData": {"a": 1}}])
def test_without_a_step_index_fields_are_unchanged(step):
    assert fields_for_step(FIELDS, step) is FIELDS


@pytest.mark.parametrize("bad", ["x", -1, "1.5"])
def test_bad_step_index_is_rejected(bad):
    with pytest.raises(ValueError):
        fields_for_step(FIELDS, {"index": bad})


def test_step_index_accepts_numeric_strings():
    assert fields_for_step(FIELDS, {"index": "2"})["member"]["index"] == 2


def test_date_values():
    today = date(2026, 10, 31)
    assert parse_date_value("+30d", today) == date(2026, 11, 30)
    assert parse_date_value("-7d", today) == date(2026, 10, 24)
    assert parse_date_value("today", today) == today
    assert parse_date_value("11/30/2026") == parse_date_value("2026-11-30") == date(2026, 11, 30)
    for bad in ("", "tomorrow", "13/40/2026", "2026-02-30"):
        with pytest.raises(ValueError):
            parse_date_value(bad, today)


def test_months_to_reads_calendar_headers():
    assert months_to("November 2026", date(2027, 1, 15)) == 2
    assert months_to("Nov 2026", date(2026, 9, 1)) == -2
    assert months_to("December, 2026", date(2026, 12, 5)) == 0
    with pytest.raises(ValueError):
        months_to("no month here", date(2026, 1, 1))
