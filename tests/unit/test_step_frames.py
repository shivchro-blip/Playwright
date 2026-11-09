"""Step time travel: the frames a run saved, the last passing run, and where two runs part."""
from __future__ import annotations

import json
from pathlib import Path

from utils.step_capture import folder_name
from webapp.services.run_manager import Run, RunConfig, TestResult
from webapp.services.step_frames import Frame, compare, frames, last_pass

NODE = "tests/dynamic_test_suite.py::test_case[chromium-TC-1]"


def make_run(tmp: Path, rid: str, at: str, outcome: str, steps: list[dict] | None, env: str = "uat") -> Run:
    run = Run(id=rid, config=RunConfig(system="S", env=env, tags=["t"]), dir=tmp / rid, status=outcome,
              created_at=at, finished_at=at)
    run.results[NODE] = TestResult(NODE, "TC-1", "chromium", outcome, 3.0)
    if steps is not None:
        folder = run.results_dir / "steps" / folder_name(NODE)
        folder.mkdir(parents=True)
        for s in steps:
            (folder / f"{s['n']}.json").write_text(json.dumps({"kind": "page", "image": True, "html": True, **s}))
        (folder / "broken.json").write_text("{nope")
    return run


def test_folder_names_are_safe_and_distinct_per_browser():
    assert folder_name(NODE) == "tests_dynamic_test_suite.py_test_case_chromium-TC-1"
    assert folder_name(NODE) != folder_name(NODE.replace("chromium", "firefox"))
    assert "/" not in folder_name("../../etc/passwd")


def test_frames_are_read_in_step_order_with_urls(tmp_path):
    run = make_run(tmp_path, "r2", "2026-11-08T10:00:00", "failed", [
        {"n": 2, "label": "Payment", "status": "failed", "seconds": 1.5, "error": "boom", "image": False},
        {"n": 1, "label": "Home", "status": "passed", "seconds": 1.0, "url": "http://x/a"}])
    got = frames(run, NODE)
    assert [f.n for f in got] == [1, 2]
    assert got[0].image == f"/runs/r2/allure-results/steps/{folder_name(NODE)}/1.jpg"
    assert got[1].image == "" and got[1].html.endswith("/2.html") and got[1].error == "boom"
    assert frames(run, "other-node") == []


def test_the_last_pass_is_the_latest_earlier_passing_run_with_pictures(tmp_path):
    steps = [{"n": 1, "label": "Home", "status": "passed", "seconds": 1.0}]
    now = make_run(tmp_path, "r4", "2026-11-08T12:00:00", "failed", steps)
    runs = [now,
            make_run(tmp_path, "r3", "2026-11-08T11:00:00", "passed", None),  # passed, but no pictures
            make_run(tmp_path, "r2", "2026-11-08T10:00:00", "passed", steps, env="dev"),  # another environment
            make_run(tmp_path, "r1", "2026-11-08T09:00:00", "passed", steps),
            make_run(tmp_path, "r0", "2026-11-08T08:00:00", "passed", steps)]
    assert last_pass(runs, now, "TC-1", "chromium") == (runs[3], NODE)
    assert last_pass(runs, now, "TC-1", "firefox") is None


def f(n, status="passed", seconds=1.0, url="http://x/a", title="A", error=""):
    return Frame(n, f"Step{n}", "page", status, seconds, url, title, error)


def test_compare_says_where_the_runs_part():
    now = [f(1), f(2, seconds=9.0), f(3, url="http://x/error?id=4"), f(4, "failed", error="Timeout")]
    then = [f(1), f(2, seconds=2.0), f(3, url="http://x/b"), f(4), f(5)]
    diffs = {d.n: d.notes for d in compare(now, then)}
    assert 1 not in diffs
    assert diffs[2] == ["slower: 9.0 s (was 2.0 s)"]
    assert diffs[3] == ["ended on another page: http://x/error (was http://x/b)"]
    assert diffs[4] == ["failed here: Timeout"]
    assert diffs[5] == ["not reached in this run"]
    assert compare([f(1, seconds=1.5)], [f(1)]) == []  # a little slower is not worth a note
    assert compare([f(1, title="B")], [f(1)])[0].notes == ["page title changed: 'B' (was 'A')"]
