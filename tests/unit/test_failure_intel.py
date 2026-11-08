"""Failure intelligence: failures grouped by cause across tests and runs, and explained with their evidence."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from webapp.services.failure_intel import FailureIntel, error_kind, failures_of, summary
from webapp.services.run_artifacts import FailurePoint, TestDetails
from webapp.services.run_manager import Run, RunConfig, TestResult


@pytest.mark.parametrize("message, kind", [
    ("Locator.click: Timeout 30000ms exceeded.\nwaiting for locator('#zip')", "not-found"),
    ("Error: strict mode violation: locator('input') resolved to 2 elements", "several"),
    ("page.goto: net::ERR_CONNECTION_REFUSED at http://uat.test/", "unreachable"),
    ("Failure: Page Config: Apply not found!", "page-missing"),
    ("Failed to generate Actions - No Field Config for: zip", "field-missing"),
    ("Failure: Unable to confirm text on screen: Done", "text"),
    ("element is not visible", "hidden"),
    ("Failure: fill on Element 'zip' failed action_helper.check_element_ready.", "not-found"),
])
def test_error_kinds(message, kind):
    assert error_kind(message)[0] == kind


def test_other_errors_are_masked_so_runs_compare():
    a = error_kind("ValueError: order 4711 for 'Ann' at https://x.test/a failed")
    b = error_kind("ValueError: order 98 for 'Bob' at https://x.test/b failed")
    assert a == b and a[0] == "other" and "4711" not in a[1] and "Ann" not in a[1]
    assert error_kind("")[1] == "failed without a message"


def make_run(rid: str, at: str, env: str, results: dict[str, tuple[str, str]]) -> Run:
    """results: test id -> (outcome, message)"""
    run = Run(id=rid, config=RunConfig(system="SYS", env=env, tags=["t"]), dir=Path("/nonexistent") / rid,
              status="failed", created_at=at, finished_at=at)
    for tid, (outcome, message) in results.items():
        run.results[tid] = TestResult(tid, tid, "chromium", outcome, 1.0, message)
    return run


def point(screen, field_name, action="FILL"):
    return TestDetails("", "", "failed", failure=FailurePoint(screen=screen, field_name=field_name, action=action))


TIMEOUT = "Locator.fill: Timeout 30000ms exceeded."


@pytest.fixture
def intel():
    runs = [
        make_run("r4", "2026-11-08T10:00:00", "uat", {"T1": ("failed", TIMEOUT), "T2": ("failed", TIMEOUT),
                                                     "T3": ("failed", "net::ERR_CONNECTION_REFUSED"),
                                                     "T4": ("passed", "")}),
        make_run("r3", "2026-11-08T09:00:00", "uat", {"T1": ("failed", TIMEOUT), "T2": ("failed", TIMEOUT),
                                                     "T3": ("passed", ""), "T4": ("passed", "")}),
        make_run("r2", "2026-11-08T08:00:00", "uat", {"T1": ("passed", ""), "T2": ("passed", ""),
                                                     "T3": ("passed", ""), "T4": ("passed", "")}),
        make_run("d1", "2026-11-08T09:30:00", "dev", {"T1": ("passed", ""), "T2": ("passed", "")}),
    ]
    points = {"r4": {("T1", "chromium"): point("Applicant", "zip"), ("T2", "chromium"): point("Applicant", "zip")},
              "r3": {("T1", "chromium"): point("Applicant", "zip"), ("T2", "chromium"): point("Applicant", "zip")}}

    def query(system, env):
        return sorted([r for r in runs if env is None or r.config.env == env], key=lambda r: r.created_at,
                      reverse=True)

    def changed(system, screen, test_id):
        return [("Pages/SYS/Applicant.json", ["2026-11-08T08:30:00", "2026-11-07T12:00:00"])] if screen else []

    return FailureIntel(query, lambda run: points.get(run.id, {}), changed)


def test_failures_with_one_cause_are_one_group_and_explained(intel):
    groups = intel.groups("SYS", "uat")
    zip_group = groups[0]
    assert zip_group.title == "2 tests fail on Applicant > zip: element not found in time"
    assert zip_group.runs == ["r4", "r3"] and len(zip_group.failures) == 4
    assert zip_group.new_since == "r3" and zip_group.steady
    assert zip_group.passing_envs == ["dev"]
    assert zip_group.changes == [{"file": "Pages/SYS/Applicant.json", "at": "2026-11-08 08:30"}]  # after r2 passed
    text = " ".join(zip_group.explanation)
    assert "Only uat: the same tests pass in dev" in text and "one fix on the page mends them all" in text
    assert "New since run r3" in text and "edited at 2026-11-08 08:30" in text
    assert "Check zip's locator on Applicant" in zip_group.suggestion
    unreachable = groups[1]
    assert unreachable.kind == "unreachable" and unreachable.tests == ["T3"] and "is up and reachable" in \
        unreachable.suggestion
    assert summary(groups) == {"groups": 2, "failures": 5, "tests": 3}


def test_one_run_is_explained_against_the_runs_before_it(intel):
    groups = intel.groups("SYS", "uat", run_id="r3")
    assert [g.title for g in groups] == ["2 tests fail on Applicant > zip: element not found in time"]
    assert groups[0].runs == ["r3"] and groups[0].new_since == "r3"
    assert intel.groups("SYS", "uat", run_id="nope") == []


def test_coming_and_going_is_called_flaky():
    runs = [make_run(f"r{i}", f"2026-11-08T0{i}:00:00", "uat", {"T1": ("failed" if i % 2 else "passed", TIMEOUT)})
            for i in range(1, 7)]
    intel = FailureIntel(lambda s, e: sorted(runs, key=lambda r: r.created_at, reverse=True), lambda run: {})
    (group,) = intel.groups("SYS", "uat")
    assert not group.steady and "Comes and goes" in " ".join(group.explanation)
    assert group.where == "(where is unknown)"


def test_where_falls_back_to_the_failed_step():
    run = make_run("r1", "2026-11-08T01:00:00", "uat", {"T1": ("failed", "")})
    run.results["T1"].steps = [{"n": 1, "kind": "screen", "label": "Payment", "status": "failed",
                                "error": "Locator.click: Timeout 5000ms exceeded."}]
    (f,) = failures_of(run, {})
    assert f.screen == "Payment" and f.kind == "not-found"
    assert failures_of(SimpleNamespace(**{**run.__dict__, "results": {}}), {}) == []
