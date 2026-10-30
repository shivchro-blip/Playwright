"""Real runs through RunManager (the web app's run path): pytest subprocess, headless Chromium, local page."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from webapp.services.run_artifacts import RunArtifacts
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
    details = RunArtifacts().details(run.id, run.results_dir)[("TC-SMOKE-003-BadAutoGen", "chromium")]
    assert "doesNotExist" in details.message and details.log, details
    assert details.has_artifacts


def test_failure_details_include_screenshot_and_dom_snapshot(workspace, tmp_path):
    run = run_and_wait(workspace, tmp_path, tags=["missing"])
    assert run.status == "failed", "\n".join(run.log[-80:])
    details = RunArtifacts().details(run.id, run.results_dir)[("TC-SMOKE-004-MissingElement", "chromium")]
    assert details.message and details.by_kind("image") and details.by_kind("html"), details
    shot = details.by_kind("image")[0]
    assert (run.results_dir / shot.url.rpartition("/")[2]).read_bytes().startswith(b"\x89PNG")
    report = run.summary_file.read_text(encoding="utf-8")  # built-in report, written when the run finished
    assert "TC-SMOKE-004-MissingElement" in report and f'src="allure-results/{shot.url.rpartition("/")[2]}"' in report


def test_log_level_decides_which_lines_the_console_gets(workspace, tmp_path):
    quiet = run_and_wait(workspace, tmp_path, tags=["smoke"], options={"headlessMode": True, "LOG_LEVEL": "WARNING"})
    assert quiet.status == "passed", "\n".join(quiet.log[-60:])
    assert not [line for line in quiet.log if " INFO " in line], "\n".join(quiet.log[:60])
    assert any("PASSED" in line for line in quiet.log)  # pytest's own result lines are not log records

    chatty = run_and_wait(workspace, tmp_path / "again", tags=["smoke"],
                          options={"headlessMode": True, "LOG_LEVEL": "INFO"})
    assert chatty.status == "passed"
    assert [line for line in chatty.log if " INFO " in line]
