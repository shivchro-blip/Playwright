"""Trends: pass rate by day, slowest tests, where failures happen."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from webapp.services import trends
from webapp.services.run_manager import Run, RunConfig, TestResult

TODAY = date(2026, 11, 5)


def run(tmp_path, rid, day, status, results, env="dev", system="S"):
    r = Run(id=rid, config=RunConfig(system=system, env=env, tags=["t"]), dir=tmp_path, status=status,
            created_at=f"{day}T10:00:00", started_at=f"{day}T10:00:00")
    for i, (test_id, outcome, seconds) in enumerate(results):
        r.results[f"n{i}"] = TestResult(f"n{i}", test_id, "chromium", outcome, seconds)
    return r


def sample(tmp_path):
    return [
        run(tmp_path, "r1", "2026-11-03", "passed", [("TC-A", "passed", 2.0), ("TC-B", "passed", 10.0)]),
        run(tmp_path, "r2", "2026-11-04", "failed", [("TC-A", "failed", 3.0), ("TC-B", "passed", 14.0)]),
        run(tmp_path, "r3", "2026-11-04", "passed", [("TC-A", "passed", 1.0), ("TC-C", "xfailed", 0.5)]),
        run(tmp_path, "r4", "2026-11-05", "running", [("TC-A", "running", 0.0)]),   # not finished: ignored
        run(tmp_path, "r5", "2026-11-04", "passed", [("TC-A", "passed", 1.0)], env="test"),
        run(tmp_path, "r6", "2026-10-01", "passed", [("TC-A", "passed", 1.0)]),    # outside a 7 day period
    ]


def test_daily_counts_results_per_day_and_fills_empty_days(tmp_path):
    points = trends.daily(sample(tmp_path), 7, "S", "dev", today=TODAY)
    assert [p.day for p in points][0] == "2026-10-30" and points[-1].day == "2026-11-05" and len(points) == 7
    by_day = {p.day: p for p in points}
    assert (by_day["2026-11-03"].passed, by_day["2026-11-03"].failed, by_day["2026-11-03"].rate) == (2, 0, 100.0)
    assert (by_day["2026-11-04"].passed, by_day["2026-11-04"].failed, by_day["2026-11-04"].runs) == (3, 1, 2)
    assert by_day["2026-11-04"].rate == 75.0
    assert by_day["2026-11-05"].total == 0 and by_day["2026-11-05"].rate is None  # only an unfinished run
    assert trends.summary(points) == {"runs": 3, "results": 6, "rate": 83.3}


def test_environment_filter_and_all_environments(tmp_path):
    runs = sample(tmp_path)
    both = trends.summary(trends.daily(runs, 7, "S", None, today=TODAY))
    only_test = trends.summary(trends.daily(runs, 7, "S", "test", today=TODAY))
    assert both["runs"] == 4 and only_test == {"runs": 1, "results": 1, "rate": 100.0}


def test_slowest_averages_over_finished_runs_and_skips_zero_durations(tmp_path):
    rows = trends.slowest(sample(tmp_path), 5, "S", "dev")
    assert [(r.test_id, r.average, r.slowest, r.latest, r.runs) for r in rows] == [
        ("TC-B", 12.0, 14.0, 14.0, 2), ("TC-A", 1.8, 3.0, 1.0, 4), ("TC-C", 0.5, 0.5, 0.5, 1)]
    assert len(trends.slowest(sample(tmp_path), 1, "S", "dev")) == 1


def test_failure_spots_group_by_screen_field_and_action(tmp_path):
    runs = [run(tmp_path, "a", "2026-11-01", "failed", [("TC-1", "failed", 1), ("TC-2", "failed", 1)]),
            run(tmp_path, "b", "2026-11-02", "failed", [("TC-1", "failed", 1), ("TC-3", "error", 1),
                                                         ("TC-4", "passed", 1)])]
    point = SimpleNamespace(screen="Applicant", field_name="Zip", action="FILL")
    other = SimpleNamespace(screen="Review", field_name="", action="")
    table = {"TC-1": point, "TC-2": point, "TC-3": other}

    def details_for(r):
        return {(t, "chromium"): SimpleNamespace(failure=table.get(t)) for t in table}

    spots = trends.failure_spots(runs, details_for, 15, "S")
    assert [(s.where, s.failures, len(s.tests), len(s.runs), s.last_run) for s in spots] == [
        ("Applicant > Zip (FILL)", 3, 2, 2, "b"), ("Review", 1, 1, 1, "b")]
    none = trends.failure_spots(runs, lambda r: {}, 15, "S")
    assert none[0].where == "(no step detail)" and none[0].failures == 4
