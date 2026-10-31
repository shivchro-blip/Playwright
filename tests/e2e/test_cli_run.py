"""python -m webapp.cli_run: the same engine as the Control tab, for CI. Exit code, summary and annotations."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.e2e.conftest import free_port  # noqa: F401  (conftest keeps the workspace fixture)

REPO_ROOT = Path(__file__).resolve().parents[2]


def cli(workspace: Path, tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.run(
        [sys.executable, "-m", "webapp.cli_run", "--workspace", str(workspace), "--out", str(tmp_path / "runs"),
         "--system", "SMOKE", "--env", "dev", "--browsers", "chromium", *args],
        cwd=REPO_ROOT, env={**base, **env}, capture_output=True, text=True, timeout=300)


def test_passing_run_exits_0_and_writes_the_summary(workspace: Path, tmp_path: Path):
    step_summary = tmp_path / "step-summary.md"
    done = cli(workspace, tmp_path, "--tags", "smoke", GITHUB_STEP_SUMMARY=str(step_summary))
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert "PASSED" in done.stdout and "Report:" in done.stdout
    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 1 and (runs[0] / "report.html").is_file()
    summary = (runs[0] / "summary.md").read_text(encoding="utf-8")
    assert "## AutomateNY run" in summary and ": passed" in summary and "| 2 | 2 |" in summary
    assert step_summary.read_text(encoding="utf-8").strip() == summary.strip()  # also in the job summary


def test_failing_run_exits_1_and_annotates_where_it_failed(workspace: Path, tmp_path: Path):
    done = cli(workspace, tmp_path, "--tags", "missing", "--quiet", GITHUB_ACTIONS="true")
    assert done.returncode == 1, done.stdout[-2000:] + done.stderr[-2000:]
    assert "::error title=TC-SMOKE-004-MissingElement::SmokeHome > missing (FILL)" in done.stdout
    summary = next((tmp_path / "runs").iterdir()).joinpath("summary.md").read_text(encoding="utf-8")
    assert "failed at `SmokeHome > missing (FILL)`" in summary


def test_no_matching_tests_and_missing_arguments_exit_2(workspace: Path, tmp_path: Path):
    nothing = cli(workspace, tmp_path, "--tags", "no-such-tag", "--quiet")
    assert nothing.returncode == 2, nothing.stdout[-1500:]
    summary = next((tmp_path / "runs").iterdir()).joinpath("summary.md").read_text(encoding="utf-8")
    assert "No runnable tests were selected" in summary and "### Failures" not in summary
    bare = subprocess.run([sys.executable, "-m", "webapp.cli_run", "--workspace", str(workspace)], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60,
                          env={**{k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")},
                               "AUTOMATENY_DOTENV_PATH": str(tmp_path / "empty.env")})
    assert bare.returncode == 2 and "--system" in bare.stderr
