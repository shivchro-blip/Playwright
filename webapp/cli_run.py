"""Run a tagged selection of tests without the web app, for CI and scripts.

    python -m webapp.cli_run --system NYBE2 --env dev --tags smoke --browsers chromium

It uses the same engine as the Control tab (same pytest command, per-run results folder, built-in report) and
streams the console. Credentials come from the environment (CI secrets) or the workspace .env. When it ends it
prints where the report is, writes summary.md next to it, appends that to $GITHUB_STEP_SUMMARY if set, and (on
GitHub Actions) annotates each failure. Exit code: 0 all passed, 1 test failures, 2 anything else (no tests
collected, setup or crash), 130 interrupted.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from webapp.services.env_config import LEVELS


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m webapp.cli_run", description=__doc__.split("\n\n")[0])
    p.add_argument("--system", help="System (default: systemConfig from .env)")
    p.add_argument("--env", help="Environment (default: envConfig from .env)")
    p.add_argument("--tags", help="Comma-separated tags to run (default: runTags from .env)")
    p.add_argument("--browsers", default="chromium", help="Comma-separated browsers (default: chromium)")
    p.add_argument("--workers", type=int, default=1, help="Parallel workers (pytest-xdist)")
    p.add_argument("--test-id", action="append", default=[], metavar="ID",
                   help="Only this test case ID (repeatable); the tags must still match")
    p.add_argument("--headed", action="store_true", help="Show the browser (default: headless)")
    p.add_argument("--log-level", choices=LEVELS, default="INFO", help="Console log level (default: INFO)")
    p.add_argument("--workspace", help="Workspace folder (default: AUTOMATENY_WORKSPACE or the repo's workspace)")
    p.add_argument("--out", help="Folder for run results (default: <workspace>/reports/runs)")
    p.add_argument("--timeout-minutes", type=float, default=0, help="Cancel the run after this long (0: no limit)")
    p.add_argument("--quiet", action="store_true", help="Do not stream the console")
    return p


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.workspace:
        os.environ["AUTOMATENY_WORKSPACE"] = str(Path(args.workspace).resolve())

    from webapp.services.env_config import EnvConfigService
    from webapp.services.run_artifacts import RunArtifacts
    from webapp.services.run_insights import build_markdown
    from webapp.services.run_manager import RunConfig, RunManager
    from webapp.settings import get_settings

    s = get_settings()
    saved = EnvConfigService(s.dotenv_path).load()
    system, env = args.system or saved["systemConfig"], args.env or saved["envConfig"]
    tags = _split(args.tags) or _split(saved["runTags"])
    if not (system and env and tags):
        print("Give --system, --env and --tags (or set systemConfig, envConfig and runTags in .env).",
              file=sys.stderr)
        return 2
    runs_dir = Path(args.out).resolve() if args.out else s.resolved_runs_dir
    manager = RunManager(repo_root=s.repo_root, workspace=s.workspace, runs_dir=runs_dir, python_exe=s.python_exe,
                         allure_cmd=s.allure_cmd, max_concurrent=1)
    config = RunConfig(system=system, env=env, tags=tags, browsers=_split(args.browsers) or ["chromium"],
                       workers=max(1, args.workers), test_ids=list(args.test_id),
                       options={"headlessMode": not args.headed, "LOG_LEVEL": args.log_level})
    try:
        run = manager.start(config)
    except (ValueError, RuntimeError) as e:
        print(f"Could not start: {e}", file=sys.stderr)
        return 2

    deadline = time.monotonic() + args.timeout_minutes * 60 if args.timeout_minutes else None
    position = 0
    try:
        while True:
            lines, position = run.log_since(position)
            if lines and not args.quiet:
                print("\n".join(lines), flush=True)
            if not run.active and run.report_status not in ("", "generating"):
                lines, position = run.log_since(position)
                if lines and not args.quiet:
                    print("\n".join(lines), flush=True)
                break
            if deadline and time.monotonic() > deadline and run.active:
                print(f"Timed out after {args.timeout_minutes:g} minutes: cancelling.", file=sys.stderr)
                manager.cancel(run.id)
                deadline = None
            time.sleep(0.5)
    except KeyboardInterrupt:
        manager.cancel(run.id)
        print("Interrupted: run cancelled.", file=sys.stderr)
        return 130

    # The suite reports "no test matched the system, environment and tags" as a failing placeholder test: in CI
    # that is a setup error, not a test failure.
    unmatched = [k for k, r in run.results.items() if "selection_error" in r.nodeid]
    if unmatched:
        run.error = run.error or (run.results[unmatched[0]].message or "No tests matched.").splitlines()[0]
        for key in unmatched:
            del run.results[key]
        run.status = "error"
    manager.write_summary(run)
    details = RunArtifacts().details(run.id, run.results_dir)
    markdown = build_markdown(run, details)
    (run.dir / "summary.md").write_text(markdown + "\n", encoding="utf-8")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for r in sorted((r for r in run.results.values() if r.outcome in ("failed", "error")),
                        key=lambda r: r.test_id)[:10]:
            item = details.get((r.test_id, r.browser))
            where = f"{item.failure.short}: " if item and item.failure else ""
            first = ((item.message if item and item.message else r.message) or "").strip().splitlines()[:1]
            print(f"::error title={r.test_id}::{where}{first[0] if first else r.outcome}".replace("%", "%25"))
    print(f"\nRun {run.id}: {run.status}. Results: {run.dir}")
    print(f"Report: {run.summary_file}\nSummary: {run.dir / 'summary.md'}")
    return {"passed": 0, "failed": 1}.get(run.status, 2)


if __name__ == "__main__":
    sys.exit(main())
