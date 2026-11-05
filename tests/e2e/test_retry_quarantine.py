"""Retries (retryFailed) and quarantine: flaky tests are retried with a trace; quarantined ones stay green."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from tests.e2e.test_runs import outcomes, run_and_wait
from webapp.services.run_artifacts import RunArtifacts
from utils import quarantine

SUITE = Path("Tests") / "SMOKE" / "MasterRegressionSuite"


def add_flaky_test(workspace: Path) -> str:
    token = uuid.uuid4().hex[:8]
    (workspace / "Pages" / "SMOKE" / "FlakyPage.json").write_text(json.dumps(
        {"path": f"/flaky/{token}.html", "fields": {"f": {"locator": "#flaky-field", "type": "text"}}}),
        encoding="utf-8")
    (workspace / SUITE / "Flaky.json").write_text(json.dumps({"testCases": [{
        "testCaseID": "TC-FLAKY-001", "description": "Fails the first time.", "system": "SMOKE", "module": "flaky",
        "environment": ["dev"], "tags": ["flaky"],
        "steps": [{"screen": "FlakyPage", "formData": {"f": "typed"}}]}]}), encoding="utf-8")
    return token


def test_a_flaky_test_fails_then_passes_on_retry_and_is_marked(workspace: Path, tmp_path: Path):
    add_flaky_test(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["flaky"], options={"headlessMode": True, "retryFailed": 1})
    assert run.status == "passed", "\n".join(run.log[-40:])
    result = next(iter(run.results.values()))
    assert (result.test_id, result.outcome, result.retries) == ("TC-FLAKY-001", "passed", 1)
    saved = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
    assert saved["results"][0]["retries"] == 1


def test_without_retries_the_same_test_fails(workspace: Path, tmp_path: Path):
    add_flaky_test(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["flaky"], options={"headlessMode": True})
    assert run.status == "failed" and outcomes(run) == {"TC-FLAKY-001": "failed"}
    assert next(iter(run.results.values())).retries == 0


def test_a_retry_is_traced_even_when_traces_are_off(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True, "retryFailed": 1})
    assert run.status == "failed"  # still broken after the retry
    result = next(iter(run.results.values()))
    assert result.retries == 1
    details = RunArtifacts("/runs").details(run.id, run.results_dir)[("TC-SMOKE-004-MissingElement", "chromium")]
    assert details.by_kind("trace"), "the retried attempt recorded a trace"


def test_a_quarantined_test_runs_but_does_not_fail_the_run(workspace: Path, tmp_path: Path):
    quarantine.add(workspace, "TC-SMOKE-004-MissingElement", "needs a fix on the portal")
    run = run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True})
    assert run.status == "passed", "\n".join(run.log[-40:])
    result = next(iter(run.results.values()))
    assert result.outcome == "xfailed" and "quarantined: needs a fix on the portal" in result.message
    assert quarantine.remove(workspace, "TC-SMOKE-004-MissingElement")
    again = run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True})
    assert again.status == "failed"  # released: it fails the run again
