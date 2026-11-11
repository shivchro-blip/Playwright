"""What went wrong: failed tests listed with where they stopped, and a cause that belongs to the whole run said once."""
from __future__ import annotations

from webapp.services.failure_intel import error_kind
from webapp.services.failure_summary import SHOWN, summarize
from webapp.ui.control import shown_lines

LAUNCH = ("playwright._impl._errors.TargetClosedError: BrowserType.launch: Target page, context or browser has "
          "been closed")


def row(i: int, outcome: str = "failed", message: str = "", where: str = "") -> dict:
    return {"nodeid": f"n{i}", "test_id": f"TC-{i}-Name", "name": "Name", "browser": "chrome", "outcome": outcome,
            "message": message, "where": where, "has_details": True}


def test_a_run_that_passed_has_nothing_to_explain():
    assert summarize([row(1, "passed"), row(2, "skipped")]) is None


def test_failed_tests_are_listed_with_where_they_stopped():
    s = summarize([row(1, "passed"), row(2, message="Failure: fill on 'zip' failed\nCall log: ...",
                                         where="Address > zip (FILL)")])
    assert [i.test_id for i in s.failed] == ["TC-2-Name"] and s.shared is None
    assert s.failed[0].message == "Failure: fill on 'zip' failed" and s.failed[0].where == "Address > zip (FILL)"


def test_a_browser_that_would_not_start_is_one_cause_not_n_errors():
    assert error_kind(LAUNCH)[0] == "browser-start"
    assert error_kind("Looks like you launched a headed browser without having a XServer running")[0] == "browser-start"
    s = summarize([row(i, "error", LAUNCH) for i in range(1, 5)])
    assert s.shared.kind == "browser-start" and s.shared.action == "headless"
    assert "4 tests did not run" in s.shared.headline and "Headless" in s.shared.hint
    one = summarize([row(1, "error", LAUNCH)])
    assert "1 test did not run" in one.shared.headline  # even a single test: nothing ran at all


def test_site_down_and_sign_in_need_more_than_one_test_to_count_as_the_run_s_cause():
    down = "net::ERR_CONNECTION_REFUSED at http://x"
    assert summarize([row(1, message=down)]).shared is None
    assert summarize([row(1, message=down), row(2, message=down)]).shared.kind == "unreachable"
    assert summarize([row(1, message="Missing credentials: PASSWORD"),
                      row(2, message="Missing credentials: PASSWORD")]).shared.kind == "login"


def test_different_causes_are_not_merged_and_a_long_list_is_cut():
    mixed = summarize([row(1, message=LAUNCH), row(2, message="element(s) not found")])
    assert mixed.shared is None
    many = summarize([row(i, message="element(s) not found") for i in range(SHOWN + 3)])
    assert len(many.failed) == SHOWN and many.more == 3


def test_the_console_shows_the_last_lines_and_says_how_many_it_left_out():
    lines = [f"line {i}" for i in range(2000)]
    shown = shown_lines(lines)
    assert len(shown) == 1501 and shown[-1] == "line 1999" and shown[1] == "line 500"
    assert shown[0].startswith("[webapp] ... 500 earlier lines are not shown here")
    assert shown_lines(["a", "b"]) == ["a", "b"]  # nothing left out: no note
    assert shown_lines(["a"], dropped=1)[0].startswith("[webapp] ... 1 earlier line is not shown")
    assert shown_lines(lines[:1500], dropped=0) == lines[:1500]
