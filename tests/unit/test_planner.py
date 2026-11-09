"""Run what matters: the planner's choices in a time budget, and why."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from webapp.services.journeys import Journey
from webapp.services.planner import plan
from webapp.services.run_manager import Run, RunConfig, TestResult

NOW = datetime(2026, 11, 8, 12, 0, 0)


def run(rid: str, at: str, results: dict[str, tuple[str, float]]) -> Run:
    r = Run(id=rid, config=RunConfig(system="S", env="uat", tags=["t"]), dir=Path("/x") / rid, status="passed",
            created_at=at, finished_at=at)
    for tid, (outcome, seconds) in results.items():
        r.results[tid] = TestResult(tid, tid, "chromium", outcome, seconds)
    return r


RUNS = [  # newest first
    run("r3", "2026-11-08T10:00:00", {"FAIL": ("failed", 20), "FLAKY": ("passed", 10), "OK": ("passed", 10),
                                       "SLOW": ("passed", 300), "CRIT": ("passed", 60)}),
    run("r2", "2026-11-07T10:00:00", {"FAIL": ("passed", 20), "FLAKY": ("failed", 10), "OK": ("passed", 10)}),
    run("r1", "2026-11-06T10:00:00", {"FLAKY": ("passed", 10), "OK": ("passed", 10)}),
    run("r0", "2026-10-20T10:00:00", {"OLD": ("passed", 15)}),
]
TESTS = ["CRIT", "FAIL", "FLAKY", "NEW", "OK", "OLD", "SLOW"]
JOURNEYS = [Journey("pay", "Payment", "S", "critical", tests=["CRIT"]),
            Journey("reg", "Registration", "S", "high", tests=["FLAKY"])]


def test_the_plan_takes_risk_first_and_says_why():
    p = plan("S", "uat", 120, TESTS, RUNS, journeys=JOURNEYS, now=NOW)
    reasons = {x.test_id: x.reasons for x in p.chosen + p.left_out}
    assert p.chosen[0].test_id == "CRIT" and p.chosen[0].must
    assert set(p.test_ids) == {"CRIT", "FAIL", "FLAKY", "NEW", "OLD"}  # 115 s of 120
    assert reasons["FAIL"] == ["failed in its last run"]
    assert reasons["FLAKY"] == ["high journey: Registration", "flaky lately"]
    assert reasons["NEW"] == ["never run on uat"]
    assert next(x for x in p.chosen if x.test_id == "NEW").seconds == 10  # the median of all recent test times
    assert reasons["OK"] == ["nothing points to a risk"]
    assert reasons["SLOW"] == ["nothing points to a risk"]
    assert p.seconds <= 120


def test_stale_tests_and_the_budget():
    p = plan("S", "uat", 1000, TESTS, RUNS, journeys=JOURNEYS, now=NOW)
    assert "not run for 19 days" in next(x for x in p.chosen if x.test_id == "OLD").reasons
    tight = plan("S", "uat", 70, TESTS, RUNS, journeys=JOURNEYS, now=NOW)
    assert tight.test_ids[0] == "CRIT"  # 60 s of the 70: the rest by risk per second
    assert "over the budget" in next(x for x in tight.left_out if x.test_id == "FAIL").reasons
    assert any("risky test case" in n and "FAIL" in n for n in tight.notes)


def test_critical_journeys_are_never_dropped_silently():
    p = plan("S", "uat", 30, TESTS, RUNS, journeys=JOURNEYS, now=NOW)
    assert "CRIT" in p.test_ids and p.seconds > 30
    assert any("critical journeys' test cases alone" in n for n in p.notes)


def test_a_change_puts_what_it_reaches_first():
    p = plan("S", "uat", 40, TESTS, RUNS, impacted={"SLOW": False, "OK": True}, now=NOW)
    first = {x.test_id: x.reasons for x in p.chosen}
    assert first["OK"] == ["the change breaks it"]
    assert "SLOW" not in first  # reached by the change, but 300 s does not fit 40 s
    assert "the change reaches it" in next(x for x in p.left_out if x.test_id == "SLOW").reasons


def test_nothing_risky_means_an_empty_plan_that_says_so():
    calm = [run("r", "2026-11-08T10:00:00", {"OK": ("passed", 10)})]
    p = plan("S", "uat", 600, ["OK"], calm, now=NOW)
    assert p.chosen == [] and "Nothing points to a risk" in p.notes[0]
