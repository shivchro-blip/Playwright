"""Console log level: one choice for the pytest console and every framework logger."""
import logging
from pathlib import Path

import pytest

from utils.log_level import from_env
from webapp.services.run_manager import RunConfig, RunManager


@pytest.fixture
def manager(tmp_path: Path, monkeypatch) -> RunManager:
    m = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs", python_exe="python")
    monkeypatch.setattr(m, "_execute", lambda run: None)
    return m


def _config(**options) -> RunConfig:
    return RunConfig(system="GOOGLE", env="dev", tags=["smoke"], options=options)


def test_chosen_level_reaches_pytest_and_the_framework_loggers(manager: RunManager):
    run = manager.start(_config(LOG_LEVEL="WARNING"))
    assert "log_cli_level=WARNING" in manager._command(run)
    env = manager._environment(run)
    assert {env[k] for k in ("LOG_LEVEL", "TestSuiteLoggerLevel", "PageLoggerLevel", "FormHandlerLoggerLevel")} \
        == {"WARNING"}


def test_without_a_choice_runs_behave_as_before(manager: RunManager, monkeypatch):
    monkeypatch.delenv("PageLoggerLevel", raising=False)
    run = manager.start(_config())
    assert "log_cli_level=INFO" in manager._command(run)
    assert "PageLoggerLevel" not in manager._environment(run)


def test_unknown_level_is_rejected(manager: RunManager):
    with pytest.raises(ValueError):
        manager.start(_config(LOG_LEVEL="TRACE"))
    assert manager.list() == []


def test_framework_modules_read_the_level_from_the_environment(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert from_env() == logging.INFO
    for value, expected in (("debug", logging.DEBUG), ("ERROR", logging.ERROR), ("nonsense", logging.INFO)):
        monkeypatch.setenv("LOG_LEVEL", value)
        assert from_env() == expected
