"""Real runs through RunManager (the web app's run path): pytest subprocess, headless Chromium, local page."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from webapp.services.run_manager import Run, RunConfig, RunManager

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TIMEOUT = 240


def run_and_wait(workspace: Path, tmp_path: Path, **config) -> Run:
    manager = RunManager(repo_root=REPO_ROOT, workspace=workspace, runs_dir=tmp_path / "runs",
                         python_exe=sys.executable, allure_cmd="allure-not-installed")
    run = manager.start(RunConfig(**{"system": "SMOKE", "env": "dev", "browsers": ["chromium"],
                                     "options": {"headlessMode": True}, **config}))
    deadline = time.monotonic() + RUN_TIMEOUT
    while run.active or run.report_status in ("", "generating"):
        if time.monotonic() > deadline:
            manager.cancel(run.id)
            pytest.fail(f"Run {run.id} did not finish in {RUN_TIMEOUT}s:\n" + "\n".join(run.log[-80:]))
        time.sleep(0.5)
    return run


def outcomes(run: Run) -> dict[str, str]:
    return {r.test_id: r.outcome for r in run.results.values()}


def test_smoke_tag_runs_and_passes(workspace, tmp_path):
    run = run_and_wait(workspace, tmp_path, tags=["smoke"])
    assert run.status == "passed", "\n".join(run.log[-80:])
    assert outcomes(run) == {"TC-SMOKE-001-Search": "passed", "TC-SMOKE-002-NotesAutoGen": "passed"}
    assert run.collected == 2 and run.exit_code == 0
    assert (run.dir / "run.json").is_file() and (run.dir / "console.log").is_file()


def test_selected_test_ids_run_only_those(workspace, tmp_path):
    run = run_and_wait(workspace, tmp_path, tags=["smoke"], test_ids=["TC-SMOKE-002-NotesAutoGen"])
    assert run.status == "passed", "\n".join(run.log[-80:])
    assert outcomes(run) == {"TC-SMOKE-002-NotesAutoGen": "passed"}


def test_unknown_autogen_rule_fails_with_a_clear_message(workspace, tmp_path):
    run = run_and_wait(workspace, tmp_path, tags=["fail"])
    assert run.status == "failed"
    result = next(iter(run.results.values()))
    assert result.test_id == "TC-SMOKE-003-BadAutoGen" and result.outcome in ("failed", "error")
    assert "Unknown autoGen rule 'doesNotExist'" in result.message + "\n".join(run.log)
