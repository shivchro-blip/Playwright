"""Run insights: per-test trends, flaky detection, run comparison and the built-in report."""
from __future__ import annotations

from pathlib import Path

from webapp.services.run_insights import TestTrend as Trend  # aliases: pytest would try to collect Test* names
from webapp.services.run_insights import build_report, compare, flaky_tests, previous_run, summarize, trends
from webapp.services.run_manager import Run, RunConfig
from webapp.services.run_manager import TestResult as Result


def make_run(n: int, results: dict[str, tuple[str, float]], status: str = "failed", system: str = "S",
             env: str = "dev", tmp: Path = Path(".")) -> Run:
    run = Run(id=f"run-{n:02d}", config=RunConfig(system=system, env=env, tags=["t"], browsers=["chromium"]),
              dir=tmp / f"run-{n:02d}", status=status, created_at=f"2026-10-{n:02d}T10:00:00",
              started_at=f"2026-10-{n:02d}T10:00:00", finished_at=f"2026-10-{n:02d}T10:01:30")
    for test_id, (outcome, seconds) in results.items():
        run.results[test_id] = Result(nodeid=test_id, test_id=test_id, browser="chromium", outcome=outcome,
                                          duration=seconds, message="boom <b>" if outcome == "failed" else "")
    return run


def test_trend_states():
    def state(*outcomes):
        return Trend("T", "chromium", list(outcomes)).state
    assert state("passed", "failed", "passed") == "flaky"
    assert state("failed", "passed", "failed", "failed") == "flaky"
    assert state("passed", "passed", "failed") == "broke"
    assert state("failed", "failed", "passed") == "fixed"
    assert state("failed", "error") == "failing"
    assert state("passed", "skipped", "passed") == "passing"  # skips are neither pass nor fail
    assert state("skipped") == "none"


def test_trends_use_finished_runs_of_one_system_env_and_a_window():
    runs = [make_run(i, {"A": ("passed" if i % 2 else "failed", 1.0)}) for i in range(1, 15)]
    runs.append(make_run(20, {"A": ("failed", 1.0)}, status="running"))  # not finished: ignored
    runs.append(make_run(21, {"A": ("failed", 1.0)}, system="OTHER"))
    runs.append(make_run(22, {"A": ("cancelled", 1.0)}, status="failed"))  # a cancelled result says nothing
    t = trends(runs, "S", "dev")[("A", "chromium")]
    assert len(t.outcomes) == 10 and t.run_ids[-1] == "run-14" and t.flaky
    assert trends(runs, "S", "dev", until="2026-10-03T10:00:00")[("A", "chromium")].outcomes == [
        "passed", "failed", "passed"]
    assert [x.test_id for x in flaky_tests(runs, "S", "dev")] == ["A"]
    assert flaky_tests(runs, "OTHER") == []


def test_compare_classifies_every_change():
    before = make_run(1, {"new": ("passed", 1), "fix": ("failed", 1), "still": ("error", 1), "slow": ("passed", 2),
                          "fast": ("passed", 10), "skip": ("passed", 1), "gone": ("passed", 1), "same": ("passed", 2)})
    after = make_run(2, {"new": ("failed", 1), "fix": ("passed", 1), "still": ("failed", 1), "slow": ("passed", 3.5),
                         "fast": ("passed", 4), "skip": ("skipped", 0), "added": ("passed", 1), "same": ("passed", 2.4),
                         })
    rows = {r.test_id: r.change for r in compare(before, after)}
    assert rows == {"new": "new failure", "fix": "fixed", "still": "still failing", "slow": "slower",
                    "fast": "faster", "skip": "status changed", "added": "added", "gone": "removed",
                    "same": "unchanged"}
    ordered = compare(before, after)
    assert ordered[0].change == "new failure" and ordered[-1].change == "unchanged"
    assert summarize(ordered)["new failure"] == 1


def test_previous_run_is_the_latest_earlier_finished_run_of_the_same_target():
    runs = [make_run(1, {}), make_run(2, {}, system="OTHER"), make_run(3, {}, status="cancelled"), make_run(4, {})]
    assert previous_run(runs, runs[3]).id == "run-01"
    assert previous_run(runs, runs[0]) is None


def test_report_is_escaped_and_shows_trend_flaky_and_comparison(tmp_path):
    runs = [make_run(1, {"A": ("passed", 1)}), make_run(2, {"A": ("failed", 1)}), make_run(3, {"A": ("passed", 1)}),
            make_run(4, {"A": ("failed", 1), "<script>": ("passed", 1)})]
    page = build_report(runs[3], runs)
    assert page.startswith("<!doctype html>") and "<script>" not in page and "&lt;script&gt;" in page
    assert "boom &lt;b&gt;" in page and 'class="tag flaky"' in page
    assert "Compared with the previous run" in page and "1 new failure" in page
    assert "Trend: last 4 runs of S/dev" in page and "<svg" in page
    lone = build_report(runs[0], runs[:1])
    assert "Trend appears after two finished runs" in lone
