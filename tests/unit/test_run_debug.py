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
