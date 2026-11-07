"""The results index follows real runs: saved on finish, read back as saved, gone with the run."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.e2e.test_runs import REPO_ROOT, run_and_wait
from webapp.services.run_manager import index_path, RunManager


def test_a_finished_run_is_in_the_index_as_saved(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["smoke", "missing"])
    runs_dir = run.dir.parent
    assert (index_path(runs_dir)).exists()
    reopened = RunManager(repo_root=REPO_ROOT, workspace=workspace, runs_dir=runs_dir, python_exe=sys.executable,
                          allure_cmd="allure-not-installed")
    [back] = reopened.index.runs(runs_dir=runs_dir)
    assert back.to_dict() == json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
    assert [r.id for r in reopened.query("SMOKE", "dev", finished_only=True)] == [run.id]
    assert reopened.query("OTHER") == []
    history = {h["outcome"] for h in reopened.index.test_history("TC-SMOKE-004-MissingElement")}
    assert history == {"failed"}
    assert reopened.delete(run.id)
    assert reopened.index.counts() == {"runs": 0, "results": 0}
