"""Step events: each test's steps with their result and time, the failing step, and the live step."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from tests.e2e.test_runs import REPO_ROOT, run_and_wait
from webapp.services.run_manager import RunConfig, RunManager


def test_steps_are_recorded_with_their_result_and_time(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["smoke", "missing"])
    by_test = {r.test_id: r for r in run.results.values()}
    ok = by_test["TC-SMOKE-001-Search"].steps
    assert [(s["n"], s["of"], s["kind"], s["label"], s["status"]) for s in ok] == \
        [(1, 1, "page", "SmokeHome", "passed")]
    assert isinstance(ok[0]["seconds"], (int, float)) and ok[0]["seconds"] >= 0
    bad = by_test["TC-SMOKE-004-MissingElement"].steps
    assert [(s["label"], s["status"]) for s in bad] == [("SmokeHome", "failed")]
    assert bad[0]["error"]  # why it failed, from the step itself
    assert all(r.step == "" for r in run.results.values())  # nothing left "running" after the run
    saved = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
    assert {r["test_id"]: len(r["steps"]) for r in saved["results"]}["TC-SMOKE-001-Search"] == 1


def test_the_step_a_running_test_is_on_is_known_while_it_runs(workspace: Path, tmp_path: Path):
    suite = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "SmokeTests.json"
    data = json.loads(suite.read_text(encoding="utf-8"))
    data["testCases"].append({"testCaseID": "TC-SMOKE-SLOW", "system": "SMOKE", "module": "smoke", "tags": ["slow"],
                              "steps": [
        {"screen": "SmokeHome", "formData": {"searchBox": "one"}},
        {"screen": "SmokeHome", "formData": {"notes": {"type": "wait", "value": "4"}}},
        {"screen": "SmokeHome", "formData": {"searchBox": "three"}}]})
    suite.write_text(json.dumps(data, indent=2), encoding="utf-8")
    manager = RunManager(repo_root=REPO_ROOT, workspace=workspace, runs_dir=tmp_path / "runs",
                         python_exe=sys.executable, allure_cmd="allure-not-installed")
    run = manager.start(RunConfig(system="SMOKE", env="dev", tags=["slow"], browsers=["chromium"],
                                  options={"headlessMode": True}))
    seen: set[str] = set()
    deadline = time.monotonic() + 240
    while (run.active or run.report_status in ("", "generating")) and time.monotonic() < deadline:
        manager.refresh_steps(run.id)
        seen |= {r.step for r in run.results.values() if r.step}
        time.sleep(0.2)
    assert run.status == "passed", "\n".join(run.log[-40:])
    assert any(s.startswith("2 of 3: SmokeHome") for s in seen), seen  # caught on the slow step
    steps = next(iter(run.results.values())).steps
    assert [s["status"] for s in steps] == ["passed"] * 3 and steps[1]["seconds"] >= 3
