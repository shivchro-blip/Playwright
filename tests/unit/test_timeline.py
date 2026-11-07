"""A test's timeline: its results with the step and reason, heals and fallbacks, quarantine and edits, newest first."""
import json

from webapp.services.run_manager import Run, RunConfig
from webapp.services.run_manager import TestResult as Result
from webapp.services.timeline import build

NODE = "tests/dynamic_test_suite.py::test[chromium-{}]"


def make_run(tmp_path, n, outcome, *, test="TC-1", env="qa", system="S", seconds=2.0, retries=0, steps=None,
             message="", heals=(), identity=()):
    run = Run(id=f"r{n:02}", config=RunConfig(system=system, env=env, tags=[]), dir=tmp_path / f"r{n:02}",
              status="passed", created_at=f"2026-11-{n:02}T09:00:00", finished_at=f"2026-11-{n:02}T09:05:00")
    run.results[NODE.format(test)] = Result(NODE.format(test), test, "chromium", outcome, seconds, message, retries,
                                            steps=steps or [])
    run.results_dir.mkdir(parents=True)
    for kind, records in (("heals", heals), ("identity", identity)):
        if records:
            (run.results_dir / f"autoplay-{kind}-1.jsonl").write_text(
                "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return run


def test_results_are_told_newest_first_with_the_failing_step_and_reason(tmp_path):
    runs = [make_run(tmp_path, 3, "failed", message="Timeout 30000ms\nCall log: ...",
                     steps=[{"n": 1, "of": 2, "label": "Home", "status": "passed"},
                            {"n": 2, "of": 2, "label": "Payment", "status": "failed"}]),
            make_run(tmp_path, 2, "passed", retries=1, message="flaky"),
            make_run(tmp_path, 1, "passed")]
    tl = build("TC-1", "S", runs)
    assert [e.run_id for e in tl.entries] == ["r03", "r02", "r01"]
    assert tl.entries[0].title == "Failed" and tl.entries[0].tone == "failed"
    assert tl.entries[0].detail == "step 2 of 2: Payment · Timeout 30000ms"
    assert tl.entries[1].detail == "flaky · passed after 1 retry"
    assert tl.outcomes == ["passed", "passed", "failed"] and tl.pass_rate == 67
    assert tl.flaky and tl.state == "flaky"  # passed only after a retry


def test_heals_fallbacks_quarantine_and_edits_join_the_story(tmp_path):
    runs = [make_run(tmp_path, 2, "passed",
                     heals=[{"test_id": "TC-1", "screen": "Home", "field": "go", "new": {"label": "Go"},
                             "score": 80, "at": "2026-11-02T09:01:00"}],
                     identity=[{"test_id": "TC-1", "screen": "Home", "field": "q", "outcome": "fallback",
                                "locator": {"locator": "input[name='q']"}, "at": "2026-11-02T09:02:00"},
                               {"test_id": "TC-1", "screen": "Home", "field": "q", "outcome": "ok"},
                               {"test_id": "TC-2", "screen": "Home", "field": "q", "outcome": "missing"}])]
    audit = [{"at": "2026-11-03T10:00:00", "user": "ann", "action": "quarantine.add", "target": "TC-1",
              "reason": "date picker"},
             {"at": "2026-11-04T10:00:00", "user": "bob", "action": "quarantine.release", "target": "TC-1"},
             {"at": "2026-11-01T08:00:00", "user": "ann", "action": "testcase.save", "target": "S/a.json",
              "test_id": "TC-1"},
             {"at": "2026-11-01T08:00:00", "user": "ann", "action": "testcase.save", "test_id": "TC-9"},
             {"at": "2026-11-01T08:00:00", "user": "ann", "action": "run.start", "target": "TC-1"}]
    tl = build("TC-1", "S", runs, audit, quarantine={"TC-1": {"reason": "date picker", "since": "2026-11-03"}})
    kinds = [(e.kind, e.who) for e in tl.entries]
    assert kinds == [("release", "bob"), ("quarantine", "ann"), ("result", ""), ("fallback", ""), ("heal", ""),
                     ("edit", "ann")]
    assert tl.entries[1].detail == "date picker"
    assert "input[name='q']" in tl.entries[3].detail
    assert tl.quarantine == {"reason": "date picker", "since": "2026-11-03"}


def test_scope_data_sets_and_durations(tmp_path):
    runs = [make_run(tmp_path, n, "passed", test="TC-1[gold]" if n % 2 else "TC-1", seconds=float(n))
            for n in range(12, 0, -1)]
    runs.append(make_run(tmp_path, 20, "failed", env="prod"))
    runs.append(make_run(tmp_path, 21, "failed", system="OTHER"))
    tl = build("TC-1[gold]", "S", runs, env="qa")
    assert tl.test_id == "TC-1" and tl.results == 12 and tl.pass_rate == 100 and tl.state == "passing"
    assert tl.entries[0].title == "Passed" and tl.entries[1].title == "Passed (TC-1[gold])"
    assert tl.typical_seconds == 7.0 and tl.slowing  # 3..7 s, then 8..12 s
    assert build("TC-1", "S", [], quarantine={}).to_dict()["pass_rate"] is None
