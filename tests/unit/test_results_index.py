"""The results index answers history questions exactly as filtering the in-memory runs did."""
import json
import os
import random
import shutil
import sqlite3
from pathlib import Path

import pytest

from webapp.services.results_index import MIGRATIONS, ResultsIndex
from webapp.services.run_manager import Run, RunConfig, RunManager, filter_runs, index_path, run_from_dict
from webapp.services.run_manager import TestResult as Result

STATUSES = ["queued", "running", "passed", "failed", "error", "cancelled"]


def make_run(runs_dir, n, system="sys", env="qa", status="passed", created="2026-11-01T10:00:00", trigger="",
             tests=("TC-1",)):
    run = Run(id=f"run-{n:03}", config=RunConfig(system=system, env=env, tags=["smoke"]), dir=runs_dir / f"run-{n:03}",
              status=status, created_at=created, finished_at=created if status in ("passed", "failed") else "",
              trigger=trigger)
    for t in tests:
        node = f"tests/dynamic_test_suite.py::test[{t}-chrome]"
        run.results[node] = Result(node, t, "chrome", "passed" if status == "passed" else "failed", 1.5,
                                       steps=[{"n": 1, "of": 1, "kind": "screen", "label": "Home",
                                               "status": "passed", "seconds": 0.2, "error": ""}])
    return run


def save(run):
    run.dir.mkdir(parents=True, exist_ok=True)
    (run.dir / "run.json").write_text(json.dumps(run.to_dict()), encoding="utf-8")


@pytest.fixture
def runs_dir(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    return d


def random_runs(runs_dir, count=120, seed=7):
    rnd = random.Random(seed)
    runs = []
    for n in range(count):
        day, hour = rnd.randint(1, 28), rnd.randint(0, 23)
        runs.append(make_run(runs_dir, n, system=rnd.choice(["alpha", "beta", "gamma"]),
                             env=rnd.choice(["qa", "uat", "prod"]), status=rnd.choice(STATUSES),
                             created=f"2026-10-{day:02}T{hour:02}:{rnd.randint(0, 59):02}:00",
                             trigger=rnd.choice(["", "schedule:nightly", "schedule:hourly", "monitor:m1", "api"]),
                             tests=rnd.sample(["TC-1", "TC-2", "TC-3", "TC-4"], rnd.randint(1, 3))))
    return runs


def test_schema_is_versioned_and_reopens(runs_dir):
    db = index_path(runs_dir)
    index = ResultsIndex(db)
    assert index.version == len(MIGRATIONS)
    index.put(make_run(runs_dir, 1))
    index.close()
    again = ResultsIndex(db)
    assert again.version == len(MIGRATIONS) and again.counts() == {"runs": 1, "results": 1}


def test_a_run_read_back_is_the_run_as_saved(runs_dir):
    index = ResultsIndex(index_path(runs_dir))
    run = make_run(runs_dir, 1, tests=("TC-1", "TC-2"))
    index.put(run)
    [back] = index.runs(runs_dir=runs_dir)
    assert back.to_dict() == run.to_dict()
    assert back.to_dict() == run_from_dict(run.to_dict(), run.dir).to_dict()


def test_put_replaces_results_and_remove_cascades(runs_dir):
    index = ResultsIndex(index_path(runs_dir))
    run = make_run(runs_dir, 1, tests=("TC-1", "TC-2", "TC-3"))
    index.put(run)
    del run.results[next(iter(run.results))]
    run.status = "failed"
    index.put(run)
    assert index.counts() == {"runs": 1, "results": 2}
    assert index.ids(finished_only=True) == ["run-001"]
    index.remove("run-001")
    assert index.counts() == {"runs": 0, "results": 0}


@pytest.mark.parametrize("filters", [
    {}, {"system": "alpha"}, {"system": "beta", "env": "uat"}, {"env": "prod"},
    {"since": "2026-10-15"}, {"until": "2026-10-10T12:00:00"}, {"since": "2026-10-05", "until": "2026-10-20"},
    {"finished_only": True}, {"system": "gamma", "finished_only": True, "limit": 5},
    {"trigger": "schedule:nightly"}, {"trigger_prefix": "schedule:"}, {"trigger_prefix": "monitor:", "limit": 3},
    {"trigger": ""}, {"limit": 10}, {"system": "nope"},
])
def test_query_matches_filtering_in_memory(runs_dir, filters):
    runs = random_runs(runs_dir)
    index = ResultsIndex(index_path(runs_dir))
    for run in runs:
        index.put(run)
    system, env = filters.pop("system", None), filters.pop("env", None)
    expected = [r.id for r in filter_runs(runs, system, env, **filters)]
    assert index.ids(system, env, **filters) == expected
    assert [r.id for r in index.runs(system, env, **filters)] == expected


def test_trigger_prefix_is_literal(runs_dir):
    index = ResultsIndex(index_path(runs_dir))
    index.put(make_run(runs_dir, 1, trigger="a_b"))
    index.put(make_run(runs_dir, 2, trigger="axb"))
    index.put(make_run(runs_dir, 3, trigger="a%"))
    assert index.ids(trigger_prefix="a_") == ["run-001"]
    assert index.ids(trigger_prefix="a%") == ["run-003"]


def test_test_history(runs_dir):
    index = ResultsIndex(index_path(runs_dir))
    for n, (status, created) in enumerate([("passed", "2026-10-01T09:00:00"), ("failed", "2026-10-02T09:00:00"),
                                           ("passed", "2026-10-03T09:00:00")]):
        index.put(make_run(runs_dir, n, status=status, created=created, tests=("TC-1", "TC-2")))
    index.put(make_run(runs_dir, 9, system="other", tests=("TC-1",)))
    history = index.test_history("TC-1", system="sys", limit=2)
    assert [(h["run"], h["outcome"]) for h in history] == [("run-002", "passed"), ("run-001", "failed")]
    assert index.test_history("TC-9") == []


def test_sync_adds_updates_removes_and_skips_unchanged(runs_dir):
    a, b = make_run(runs_dir, 1), make_run(runs_dir, 2)
    save(a)
    save(b)
    (runs_dir / "broken").mkdir()
    (runs_dir / "broken" / "run.json").write_text("{not json", encoding="utf-8")
    index = ResultsIndex(index_path(runs_dir))
    assert index.sync(runs_dir) == {"added": 2, "updated": 0, "removed": 0, "unchanged": 0}
    assert index.sync(runs_dir) == {"added": 0, "updated": 0, "removed": 0, "unchanged": 2}
    a.status = "failed"
    save(a)
    os.utime(a.dir / "run.json", ns=(1, 1))  # a different mtime whatever the clock's resolution
    shutil.rmtree(b.dir)
    assert index.sync(runs_dir) == {"added": 0, "updated": 1, "removed": 1, "unchanged": 0}
    assert [r.status for r in index.runs()] == ["failed"]


def test_run_manager_keeps_the_index_in_step(tmp_path, runs_dir):
    old = make_run(runs_dir, 1, status="running")  # the app stopped mid-run
    save(old)
    save(make_run(runs_dir, 2, system="beta", created="2026-11-02T10:00:00"))
    manager = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=runs_dir, python_exe="python")
    assert (index_path(runs_dir)).exists()
    assert [r.id for r in manager.query()] == ["run-002", "run-001"]
    assert [r.id for r in manager.query("beta")] == ["run-002"]
    assert manager.query(finished_only=True)[-1].status == "error"  # marked error at start-up, and indexed so
    assert manager.query()[0] is manager.get("run-002")  # the live objects, not copies
    assert manager.delete("run-002")
    assert [r.id for r in manager.query()] == ["run-001"]
    assert manager.index.counts()["runs"] == 1


def test_run_manager_falls_back_when_the_index_breaks(tmp_path, runs_dir, monkeypatch):
    save(make_run(runs_dir, 1))
    manager = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=runs_dir, python_exe="python")

    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(manager.index, "ids", broken)
    assert [r.id for r in manager.query()] == ["run-001"]


def test_a_corrupt_index_is_rebuilt_from_the_folders(tmp_path, runs_dir):
    save(make_run(runs_dir, 1))
    (index_path(runs_dir)).write_bytes(b"this is not a database" * 100)
    manager = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=runs_dir, python_exe="python")
    assert manager.index is not None and manager.index.counts()["runs"] == 1
    assert [r.id for r in manager.query()] == ["run-001"]
    assert Path(f"{index_path(runs_dir)}.bad").exists()
