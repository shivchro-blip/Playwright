"""Pause on failure in the run manager: the run's pause state, the command it sends, and who may use it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from webapp.services.env_config import RUN_OPTION_KEYS
from webapp.services.run_manager import PREFIX, Run, RunConfig, RunManager, pytest_environment, run_from_dict


def manager(tmp_path: Path) -> RunManager:
    return RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs", python_exe="python")


def running(m: RunManager, run_id: str = "r1", **options) -> Run:
    run = Run(id=run_id, config=RunConfig(system="S", env="qa", tags=["t"], options=options),
              dir=m.runs_dir / run_id, status="running")
    run.dir.mkdir(parents=True)
    m._runs[run.id] = run
    return run


PAUSED = {"event": "debug", "phase": "paused", "seq": 4, "test": "TC-1", "step": 2, "of": 5, "field": "zip",
          "screen": "Address", "error": "not found", "can_skip": True, "has_picture": True}


def test_a_pause_event_marks_the_run_and_the_command_names_that_pause(tmp_path):
    m = manager(tmp_path)
    run = running(m, debugOnFailure=True)
    heard = []
    m.bus.subscribe(heard.append, ["run"])
    m._handle_event(run, json.dumps(PAUSED))
    assert run.paused["field"] == "zip" and run.debug_pauses == 1 and run.status == "running"
    assert [e["change"] for e in heard if e["change"] != "log"] == ["paused"]
    assert any("Paused: step 2 failed on field 'zip'" in line for line in run.log)

    assert m.debug_command("r1", "retry")
    assert json.loads((run.dir / "debug" / "command.json").read_text()) == {"do": "retry", "seq": 4}

    m._handle_event(run, json.dumps({"event": "debug", "phase": "resumed", "seq": 4, "do": "retry"}))
    assert run.paused is None and [e["change"] for e in heard if e["change"] != "log"] == ["paused", "resumed"]
    assert not m.debug_command("r1", "retry")  # it is not waiting any more

    m._handle_event(run, json.dumps({**PAUSED, "seq": 5, "notice": "Could not read the edited files"}))
    m._handle_event(run, json.dumps({**PAUSED, "seq": 6}))
    assert run.debug_pauses == 2  # a pause told again with a notice is the same pause


def test_only_known_commands_and_only_skip_when_there_is_a_field(tmp_path):
    m = manager(tmp_path)
    run = running(m)
    m._handle_event(run, json.dumps({**PAUSED, "can_skip": False}))
    assert not m.debug_command("r1", "skip") and not m.debug_command("r1", "format-disk")
    assert m.debug_command("r1", "abort") and m.debug_command("r1", "restep")
    run.status = "passed"
    assert not m.debug_command("r1", "retry")  # an ended run cannot be answered
    assert not m.debug_command("nope", "retry")


def test_debug_runs_are_for_a_person_on_this_machine_one_test_at_a_time(tmp_path):
    class Runners:  # one registered runner, so the run reaches the pause-on-failure checks
        def valid_target(self, name):
            return True

    m = manager(tmp_path)
    m.runners = Runners()
    base = dict(system="S", env="qa", tags=["t"], options={"debugOnFailure": True})
    with pytest.raises(ValueError, match="start yourself"):
        m.start(RunConfig(**base), trigger="schedule:nightly")
    with pytest.raises(ValueError, match="one test at a time"):
        m.start(RunConfig(**base, workers=2))
    with pytest.raises(ValueError, match="remote runner"):
        m.start(RunConfig(**base, runner="build-box"))
    assert "debugOnFailure" in RUN_OPTION_KEYS


def test_only_a_run_that_asked_gets_a_debug_folder_whatever_env_says(tmp_path, monkeypatch):
    m = manager(tmp_path)
    monkeypatch.setenv("debugOnFailure", "true")  # as if .env said so
    monkeypatch.setenv("debugDir", "/elsewhere")
    asked = pytest_environment(running(m, "r1", debugOnFailure=True), tmp_path)
    assert asked["debugOnFailure"] == "True" or asked["debugOnFailure"].lower() == "true"
    assert asked["debugDir"] == str(tmp_path / "runs" / "r1" / "debug")
    not_asked = pytest_environment(running(m, "r2", debugOnFailure=False), tmp_path)
    assert "debugDir" not in not_asked and not_asked["debugOnFailure"] == "false"
    plain = pytest_environment(running(m, "r3"), tmp_path)
    assert "debugDir" not in plain and plain["debugOnFailure"] == "false"


def test_the_number_of_pauses_is_kept_with_the_run(tmp_path):
    m = manager(tmp_path)
    run = running(m, debugOnFailure=True)
    run.debug_pauses = 3
    again = run_from_dict(run.to_dict(), run.dir)
    assert again.debug_pauses == 3 and again.paused is None
    assert PREFIX  # the events travel on the same prefix as the other run events


def test_a_pause_before_a_step_and_its_notes_are_kept_on_the_run(tmp_path):
    import time
    m = manager(tmp_path)
    run = running(m, debugOnFailure=True, debugPauseBefore="all")
    m._handle_event(run, json.dumps({"event": "debug", "phase": "paused", "seq": 2, "reason": "step", "step": 2,
                                     "step_abs": 2, "label": "Address", "can_skip": True, "deadline_in": 600,
                                     "cdp_port": 9333}))
    assert run.paused["reason"] == "step" and run.paused["cdp_port"] == 9333
    assert 590 < run.paused["deadline_at"] - time.time() <= 600 and run.paused["since"] <= time.time()
    assert any("Paused before step 2 (Address)" in line for line in run.log)
    m._handle_event(run, json.dumps({"event": "debug", "phase": "note", "seq": 2, "info": "State saved.",
                                     "deadline_in": 1800}))
    assert run.paused["info"] == "State saved." and run.paused["deadline_at"] - time.time() > 1700
    assert m.debug_command("r1", "go") and m.debug_command("r1", "savestate") and m.debug_command("r1", "extend")
    assert m.debug_command("r1", "skip")  # a step can be skipped
    run.paused["reason"] = "failure"
    assert not m.debug_command("r1", "go")  # "run to the end" is for stopping before steps


def test_pausing_before_steps_needs_pause_on_failure(tmp_path):
    m = manager(tmp_path)
    with pytest.raises(ValueError, match="needs Pause on failure"):
        m.start(RunConfig(system="S", env="qa", tags=["t"], options={"debugPauseBefore": "all"}))
    from webapp.services.env_config import RUN_OPTION_KEYS
    assert "debugPauseBefore" in RUN_OPTION_KEYS


def test_a_run_that_paused_is_left_out_of_trends_and_comparisons(tmp_path):
    from webapp.services.run_insights import finished, trends
    from webapp.services.run_manager import TestResult
    m = manager(tmp_path)
    clean = running(m, "r1")
    debugged = running(m, "r2")
    for run, outcome in ((clean, "passed"), (debugged, "failed")):
        run.status = "passed" if outcome == "passed" else "failed"
        run.results["n"] = TestResult("n", "TC-1", "chrome", outcome)
    debugged.debug_pauses = 2
    assert [r.id for r in finished(m.list())] == ["r1"]
    assert {r.id for r in finished(m.list(), include_debugged=True)} == {"r1", "r2"}
    assert trends(m.list())[("TC-1", "chrome")].outcomes == ["passed"]  # the failed-then-fixed run is not in it


def test_a_field_the_page_lacks_is_named_so_it_can_be_added_from_the_pause(tmp_path):
    m = manager(tmp_path)
    run = running(m, debugOnFailure=True)
    m._handle_event(run, json.dumps({
        "event": "debug", "phase": "paused", "seq": 1, "reason": "failure", "step": 15, "step_abs": 15,
        "kind": "page", "label": "Directors", "screen": "Directors", "field": "", "can_skip": False,
        "error": "Failed to generate Actions - No Field Config for: addDirectorship"}))
    assert run.paused["field"] == "addDirectorship" and run.paused["missing_field"] is True
    assert run.paused["can_skip"] is False and not m.debug_command("r1", "skip")  # nothing to leave out
    assert m.debug_command("r1", "retry")  # run again once the page has the field
    other = running(m, "r2", debugOnFailure=True)
    m._handle_event(other, json.dumps({"event": "debug", "phase": "paused", "seq": 1, "field": "zip",
                                       "error": "element not found", "can_skip": True}))
    assert other.paused["field"] == "zip" and "missing_field" not in other.paused


def test_pause_request_needs_a_running_run_started_with_pause_on_failure(tmp_path):
    m = manager(tmp_path)
    plain = running(m, "plain")
    assert "without Pause on failure" in m.request_pause("plain")
    assert not (plain.dir / "debug" / "pause_request").exists()

    run = running(m, "r2", debugOnFailure=True)
    assert m.request_pause("r2") == ""
    assert (run.dir / "debug" / "pause_request").exists()

    run.paused = {"seq": 1}
    assert "already paused" in m.request_pause("r2")
    run.paused = None
    run.status = "passed"
    assert "not running" in m.request_pause("r2") and "not running" in m.request_pause("nope")
