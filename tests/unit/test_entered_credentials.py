"""Login values typed into the app: used for that run only, never persisted, logged or overriding existing ones."""
import json
from pathlib import Path

import pytest

from webapp.services.run_manager import RunConfig, RunManager


@pytest.fixture
def manager(tmp_path: Path, monkeypatch) -> RunManager:
    m = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs", python_exe="python")
    monkeypatch.setattr(m, "_execute", lambda run: None)
    return m


def _config() -> RunConfig:
    return RunConfig(system="GOOGLE", env="dev", tags=["smoke"])


def test_entered_values_reach_the_subprocess_environment(manager, monkeypatch):
    monkeypatch.delenv("APP_USER", raising=False)
    run = manager.start(_config(), credentials={"APP_USER": "alice"})
    assert manager._environment(run)["APP_USER"] == "alice"


def test_existing_environment_value_wins(manager, monkeypatch):
    monkeypatch.setenv("APP_USER", "from-env")
    run = manager.start(_config(), credentials={"APP_USER": "typed"})
    assert manager._environment(run)["APP_USER"] == "from-env"


def test_values_are_never_saved(manager):
    run = manager.start(_config(), credentials={"APP_PASS": "s3cret-value"})
    saved = (run.dir / "run.json").read_text(encoding="utf-8")
    assert "s3cret-value" not in saved and "s3cret-value" not in json.dumps(run.to_dict())


def test_scrub_masks_values_in_output(manager):
    run = manager.start(_config(), credentials={"APP_PASS": "s3cret-value"})
    assert run.scrub("typed s3cret-value here") == "typed *** here"


def test_rerun_carries_values_in_memory(manager):
    run = manager.start(_config(), credentials={"APP_PASS": "s3cret-value"})
    run.status = "passed"
    again = manager.rerun(run.id)
    assert again._secrets == {"APP_PASS": "s3cret-value"}


def test_invalid_names_rejected(manager):
    with pytest.raises(ValueError):
        manager.start(_config(), credentials={"BAD KEY": "x"})
    assert manager.list() == []


def test_rerun_of_one_test_runs_only_that_test_on_its_browser(manager):
    config = RunConfig(system="GOOGLE", env="dev", tags=["smoke"], browsers=["chrome", "firefox"], workers=3,
                       test_ids=["TC-1", "TC-2"])
    run = manager.start(config, credentials={"APP_PASS": "s3cret-value"})
    run.status = "failed"
    again = manager.rerun_test(run.id, "TC-2[set a]", "firefox")  # a data-set suffix is dropped
    assert again.id != run.id and again.config.test_ids == ["TC-2"]
    assert again.config.browsers == ["firefox"] and again.config.workers == 1
    assert again.config.system == "GOOGLE" and again.config.env == "dev" and again.config.tags == ["smoke"]
    assert again._secrets == {"APP_PASS": "s3cret-value"}
    assert manager.rerun_test(run.id, "TC-3").config.browsers == ["chrome", "firefox"]  # no browser: the run's own
    with pytest.raises(ValueError):
        manager.rerun_test(run.id, "  ")
    with pytest.raises(ValueError):
        manager.rerun_test("nope", "TC-1")
