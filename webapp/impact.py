"""What a change reaches, from the command line (for CI): the test cases, the journeys, the untested changes.

    python -m webapp.impact --base origin/main                 # summary
    python -m webapp.impact --base origin/main --json          # the whole report
    python -m webapp.impact --base origin/main --run dev       # run only the impacted test cases

With --run it starts the impacted test cases of each system with python -m webapp.cli_run (same options and
exit codes; the worst one wins). Exit codes without --run: 0, or 3 with --fail-on-breaks when a test case uses
something the change removed, and 2 for a usage error (unknown commit, no git).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m webapp.impact", description=__doc__.split("\n\n")[0])
    p.add_argument("--base", default="", help="Commit, tag or branch to compare with (e.g. origin/main)")
    p.add_argument("--head", default="", help="Compare up to this commit (default: the files as they are now)")
    p.add_argument("--screen", action="append", default=[], metavar="SYSTEM:PAGE",
                   help="A screen the application changed (repeatable)")
    p.add_argument("--json", action="store_true", help="Print the whole report as JSON")
    p.add_argument("--run", metavar="ENV", default="", help="Run the impacted test cases on this environment")
    p.add_argument("--browsers", default="chromium", help="With --run: comma-separated browsers")
    p.add_argument("--fail-on-breaks", action="store_true",
                   help="Exit 3 when a test case uses a page, field, action or test that was removed")
    p.add_argument("--workspace", help="Workspace folder (default: AUTOPLAY_WORKSPACE or the repo's workspace)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.workspace:
        os.environ["AUTOPLAY_WORKSPACE"] = str(Path(args.workspace).resolve())
    from webapp.services.impact import ImpactError, analyze, app_changes, changes_from_git, load_model
    from webapp.services.journeys import JourneyStore
    from webapp.services.test_catalog import TestCatalogService
    from webapp.settings import get_settings

    workspace = get_settings().workspace
    screens: dict[str, list[str]] = {}
    for item in args.screen:
        system, _, page = item.partition(":")
        if not system or not page:
            print(f"--screen wants SYSTEM:PAGE, not {item!r}", file=sys.stderr)
            return 2
        screens.setdefault(system, []).append(page)
    if not args.base and not screens:
        print("Give --base (a commit or branch to compare with) or --screen SYSTEM:PAGE.", file=sys.stderr)
        return 2
    try:
        changes = (changes_from_git(workspace, args.base, args.head) if args.base else []) + app_changes(screens)
    except ImpactError as e:
        print(f"impact: {e}", file=sys.stderr)
        return 2
    report = analyze(list(dict.fromkeys(changes)), load_model(workspace), JourneyStore(workspace).list())
    if args.json:
        print(json.dumps(report.to_dict(), indent=1))
    else:
        print(f"{len(report.changes)} change(s); {len(report.tests)} test case(s) reached"
              + (f", {sum(t.breaks for t in report.tests)} will break" if any(t.breaks for t in report.tests)
                 else "") + f"; {len(report.journeys)} journey(s)")
        for j in report.journeys:
            print(f"  journey {j.name} [{j.criticality}]: {len(j.tests)} of {j.of} test cases")
        for t in report.tests:
            print(f"  {'BREAKS ' if t.breaks else ''}{t.system}/{t.test_id}: {'; '.join(t.reasons)}"
                  + ("" if t.runnable else " (cannot run: no tags or steps)"))
        for c in report.not_covered:
            print(f"  not tested: {c.label}")
    code = 3 if args.fail_on_breaks and any(t.breaks for t in report.tests) else 0
    if not args.run:
        return code
    from webapp import cli_run
    worst = 0
    for system, ids in report.selection().items():
        catalog = {t.test_id: t for t in TestCatalogService(workspace).load(system).tests}
        tags = sorted({tag for i in ids for tag in (catalog[i].tags if i in catalog else [])}, key=str.casefold)
        if not tags:
            continue
        argv = ["--system", system, "--env", args.run, "--tags", ",".join(tags), "--browsers", args.browsers]
        for i in ids:
            argv += ["--test-id", i]
        worst = max(worst, cli_run.main(argv))
    if not report.selection():
        print("Nothing to run: no test case the change reaches can run.")
    return max(worst, code)


if __name__ == "__main__":
    sys.exit(main())
