"""Checkpoints: file names, the step window from the environment, resume matching, and the run options."""
import json
from pathlib import Path

import pytest

from utils import checkpoints
from webapp.services.checkpoints import CheckpointStore
from webapp.services.run_manager import RunConfig, RunManager


def test_names_and_paths_are_safe(tmp_path):
    assert checkpoints.safe_name("TC_DTF17_001/C Corp:1") == "TC_DTF17_001_C_Corp_1"
    assert checkpoints.safe_name("../..") == "test"
    assert checkpoints.checkpoint_path(tmp_path, "TC-1", 3) == tmp_path / "TC-1" / "3.json"


def test_step_window_defaults_to_the_whole_test_and_is_clamped(monkeypatch):
    for key in ("runFromStep", "runToStep"):
        monkeypatch.delenv(key, raising=False)
    assert checkpoints.steps_window(5) == (1, 5) and not checkpoints.is_windowed(5)
    monkeypatch.setenv("runFromStep", "3")
    assert checkpoints.steps_window(5) == (3, 5) and checkpoints.is_windowed(5)
    monkeypatch.setenv("runToStep", "4")
    assert checkpoints.steps_window(5) == (3, 4)
    monkeypatch.setenv("runFromStep", "9")
    assert checkpoints.steps_window(5) == (5, 5)
    monkeypatch.setenv("runFromStep", "x")
    assert checkpoints.steps_window(5)[0] == 1


def test_resume_only_applies_to_its_own_test_case(tmp_path, monkeypatch):
    file = tmp_path / "TC-1" / "2.json"
    file.parent.mkdir()
    file.write_text(json.dumps({"test_id": "TC-1", "step": 2, "url": "http://x/", "storage_state": {},
                                "session_storage": {"origin": "http://x", "items": {"k": "v"}}}), encoding="utf-8")
    monkeypatch.setenv("resumeCheckpoint", str(file))
    assert checkpoints.load_resume("TC-1")["step"] == 2
    assert checkpoints.load_resume("TC-OTHER") is None
    assert "sessionStorage.setItem" in checkpoints.session_storage_script(checkpoints.load_resume("TC-1"))
    assert checkpoints.session_storage_script({"session_storage": {"origin": "x", "items": {}}}) is None
    monkeypatch.setenv("resumeCheckpoint", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="Cannot read"):
        checkpoints.load_resume("TC-1")
    monkeypatch.delenv("resumeCheckpoint")
    assert checkpoints.load_resume("TC-1") is None


def test_saving_needs_the_option_and_a_folder(monkeypatch):
    monkeypatch.delenv("checkpointsDir", raising=False)
    monkeypatch.setenv("saveCheckpoints", "true")
    assert not checkpoints.save_enabled()
    monkeypatch.setenv("checkpointsDir", "/somewhere")
    assert checkpoints.save_enabled()
    monkeypatch.setenv("saveCheckpoints", "false")
    assert not checkpoints.save_enabled()


def _saved(runs: Path, run_id: str, test_id: str, step: int, url: str = "http://x/") -> Path:
    file = checkpoints.checkpoint_path(runs / run_id / "checkpoints", test_id, step)
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"test_id": test_id, "step": step, "url": url, "saved": "2026-10-31T10:00:00"}),
                    encoding="utf-8")
    return file


def test_store_prefers_the_newest_run_per_step(tmp_path):
    old = _saved(tmp_path, "20261030-100000-aaaa", "TC-1", 1)
    new = _saved(tmp_path, "20261031-100000-bbbb", "TC-1", 2)
    newer1 = _saved(tmp_path, "20261031-110000-cccc", "TC-1", 1, url="http://y/")
    _saved(tmp_path, "20261031-110000-cccc", "TC-2", 1)
    found = CheckpointStore(tmp_path).for_test("TC-1")
    assert {s: c.path for s, c in found.items()} == {1: newer1, 2: new} and old not in {c.path for c in found.values()}
    assert found[1].url == "http://y/" and found[2].run_id == "20261031-100000-bbbb"
    assert CheckpointStore(tmp_path / "none").for_test("TC-1") == {}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    m = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs", python_exe="python")
    monkeypatch.setattr(m, "_execute", lambda run: None)
    return m


def _config(options, test_ids=("TC-1",)):
    return RunConfig(system="S", env="dev", tags=["t"], test_ids=list(test_ids), options=options)


def test_run_options_for_running_from_a_step_are_checked(manager, tmp_path):
    good = _saved(tmp_path / "runs", "20261031-100000-aaaa", "TC-1", 2)
    run = manager.start(_config({"runFromStep": 3, "resumeCheckpoint": str(good), "saveCheckpoints": True}))
    env = manager._environment(run)
    assert env["runFromStep"] == "3" and env["resumeCheckpoint"] == str(good) and env["saveCheckpoints"] == "true"
    assert env["checkpointsDir"] == str(run.dir / "checkpoints")
    assert manager.start(_config({"startUrl": "https://portal.example/apply", "runFromStep": 2}))


@pytest.mark.parametrize("options, test_ids, message", [
    ({"runFromStep": 3}, (), "exactly one test"),
    ({"runFromStep": 3}, ("A", "B"), "exactly one test"),
    ({"runFromStep": "x"}, ("A",), "whole number"),
    ({"runFromStep": -1}, ("A",), "negative"),
    ({"runFromStep": 4, "runToStep": 2}, ("A",), "before the first"),
    ({"startUrl": "javascript:alert(1)"}, ("A",), "http"),
    ({"resumeCheckpoint": "/etc/passwd"}, ("A",), "checkpoints"),
])
def test_bad_step_options_are_rejected(manager, options, test_ids, message):
    with pytest.raises(ValueError, match=message):
        manager.start(_config(options, test_ids))
    assert manager.list() == []


def test_a_checkpoint_outside_the_runs_folder_is_refused(manager, tmp_path):
    elsewhere = _saved(tmp_path / "other", "run", "TC-1", 1)
    with pytest.raises(ValueError, match="checkpoints"):
        manager.start(_config({"runFromStep": 2, "resumeCheckpoint": str(elsewhere)}))
