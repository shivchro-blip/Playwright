"""Quarantine file, retry command line and flaky detection (no browser)."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from utils import quarantine
from webapp.pytest_events import PREFIX
from webapp.services.run_insights import trends
from webapp.services.run_manager import Run, RunConfig, RunManager, TestResult, pytest_command


def cfg(**kw) -> RunConfig:
    return RunConfig(**{"system": "S", "env": "e", "tags": ["t"], **kw})


def test_quarantine_file_round_trip_and_data_set_ids(tmp_path):
    assert quarantine.load(tmp_path) == {}
    quarantine.add(tmp_path, "TC-1[austin]", "  flaky date picker ")
    assert list(quarantine.load(tmp_path)) == ["TC-1"]
    assert quarantine.load(tmp_path)["TC-1"]["reason"] == "flaky date picker"
    assert quarantine.base_id("TC-1[dallas]") == "TC-1" and quarantine.base_id("TC-2") == "TC-2"
    assert quarantine.remove(tmp_path, "TC-1") and not quarantine.remove(tmp_path, "TC-1")
    (tmp_path / "Data").mkdir(exist_ok=True)
    (tmp_path / "Data" / "quarantine.json").write_text("{broken", encoding="utf-8")
    assert quarantine.load(tmp_path) == {}  # an unreadable file quarantines nothing


def test_marking_adds_xfail_to_matching_items_only(tmp_path):
    quarantine.add(tmp_path, "TC-A", "why")
    markers = []

    def item(test_id):
        spec = SimpleNamespace(params={"test_case": {"testCaseID": test_id}})
        return SimpleNamespace(callspec=spec, add_marker=lambda m, t=test_id: markers.append((t, m)))

    plain = SimpleNamespace(add_marker=lambda m: markers.append(("plain", m)))
    assert quarantine.mark([item("TC-A"), item("TC-A[set]"), item("TC-B"), plain], tmp_path) == 2
    assert [t for t, _ in markers] == ["TC-A", "TC-A[set]"]
    assert markers[0][1].kwargs["reason"] == "quarantined: why" and markers[0][1].kwargs["strict"] is False


def test_retry_option_adds_reruns_to_the_command(tmp_path):
    def command(**options):
        run = Run(id="r", config=cfg(options=options), dir=tmp_path)
        return pytest_command(run, sys.executable, tmp_path)

    assert "--reruns" not in command() and "--reruns" not in command(retryFailed=0)
    cmd = command(retryFailed=2)
    assert cmd[cmd.index("--reruns") + 1] == "2"


def test_retry_events_count_attempts_and_the_final_event_carries_them(tmp_path):
    manager = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs", python_exe=sys.executable)
    run = Run(id="r", config=cfg(browsers=["chromium"]), dir=tmp_path)
    node = "suite::test[chromium-TC-1]"
    for evt in ({"event": "start", "nodeid": node}, {"event": "retry", "nodeid": node, "attempt": 1},
                {"event": "test", "nodeid": node, "when": "call", "outcome": "passed", "duration": 1, "retries": 1}):
        manager._handle_event(run, json.dumps(evt))
    result = run.results[node]
    assert (result.outcome, result.retries) == ("passed", 1)
    assert PREFIX.strip() == "@@AUTOPLAY@@"


def test_passing_only_after_a_retry_is_flaky(tmp_path):
    runs = []
    for i, (outcome, retries) in enumerate([("passed", 0), ("passed", 1), ("passed", 0)]):
        run = Run(id=f"r{i}", config=cfg(), dir=tmp_path, status="passed", created_at=f"2026-11-0{i + 1}T10:00:00")
        run.results["n"] = TestResult("n", "TC-1", "chromium", outcome, 1.0, retries=retries)
        runs.append(run)
    trend = trends(runs)[("TC-1", "chromium")]
    assert trend.flaky and trend.state == "flaky" and trend.to_dict()["retried"] == 1
    assert not trends(runs[:1])[("TC-1", "chromium")].flaky  # never retried, never flipped
