"""Build AutoPlay Lite: the smaller edition for people who only run tests someone else wrote.

    python tools/build_lite.py            # (re)build the AutomationLite folder
    python tools/build_lite.py --check    # exit 1 when the committed folder is out of date

The Lite edition has Control (run, live console, report, Allure), History and the Generator without the Recorder
and the Studio. It is the same code as the full app with `webapp/edition.py` switched to Lite, minus the files of
the sections it leaves out, so a fix in the full app reaches Lite by rebuilding. The folder is committed to git so
it can be downloaded and shared as it is.

The folder's `workspace/` is the team's: it is created from the repository's workspace the first time and never
overwritten by a rebuild (use --reset-workspace to replace it).
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_NAME = "AutomationLite"

# Copied as they are
FILES = ["conftest.py", "pytest.ini", "requirements.txt", "requirements-web.txt", "requirements-lock.txt",
         ".env.example", "tests/__init__.py", "tests/dynamic_test_suite.py"]
TREES = ["webapp", "utils", "pages"]
# Left out of webapp/: the other sections, their dialogs, the REST/runner APIs and the command-line tools
EXCLUDE = {
    "webapp/api.py", "webapp/api_contract.py", "webapp/runner_api.py", "webapp/runner_agent.py",
    "webapp/cli_run.py", "webapp/impact.py", "webapp/control",
    "webapp/ui/activity.py", "webapp/ui/app_map.py", "webapp/ui/automations.py", "webapp/ui/confidence.py",
    "webapp/ui/help.py", "webapp/ui/impact.py", "webapp/ui/journeys.py", "webapp/ui/monitors.py",
    "webapp/ui/releases.py", "webapp/ui/runners_dialog.py", "webapp/ui/schedules.py",
    "webapp/ui/studio.py", "webapp/ui/recorder_dialog.py",
}
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc")
EDITION_LINE = 'LITE = os.environ.get("AUTOPLAY_EDITION", "").strip().lower() == "lite"'


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _copy_tree(src: Path, dst: Path) -> None:
    for item in sorted(src.iterdir()):
        if item.name in ("__pycache__",) or item.suffix == ".pyc" or _rel(item) in EXCLUDE:
            continue
        if item.is_dir():
            dst.joinpath(item.name).mkdir(parents=True, exist_ok=True)
            _copy_tree(item, dst / item.name)
        else:
            shutil.copy2(item, dst / item.name)


SETUP_CMD = """@echo off
rem Runs setup.ps1 for this run only with the PowerShell execution policy bypassed (Windows refuses to run a
rem downloaded .ps1 file directly), and clears the "downloaded from the internet" mark on this folder's scripts.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0.' -File | Unblock-File"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
"""


def readme() -> str:
    return """# AutoPlay Lite

The small edition of AutoPlay for running tests that someone else wrote. Pick the system, environment and
tests on **Control**, press **Run**, watch the console and open the report and Allure when it ends.

| Section | What it is for |
|---|---|
| Control | Choose a system, environment and tests, press Run, follow the run live, open its report and Allure |
| History | Past runs, results, trends and re-runs |
| Generator | Look at and adjust test cases, pages, data sets and common actions (no Recorder, no Studio) |

## First time on a machine

1. Install Python 3.12 or newer (python.org) and tick "Add python.exe to PATH".
2. Double-click `setup.cmd` (creates `.venv`, installs the packages and the Playwright browser, and creates
   `.env`). Use it instead of `setup.ps1`: Windows refuses to run a downloaded .ps1 file directly.
3. Put the passwords your tests need in `.env` (never in a test file).
4. Put the shared test files in `workspace/` (`Tests/`, `Pages/`, `Data/`), keeping the folder layout.

## Every day

Double-click `start.cmd` (or the "Start AutoPlay Lite" desktop shortcut). The app opens at
http://127.0.0.1:8080.

## For the person who maintains it

This folder is generated from the full AutoPlay repository: `python tools/build_lite.py` rebuilds it, and
`python tools/build_lite.py --check` tells whether it is up to date. Do not edit the code here; change the full
app and rebuild. `workspace/` is the only part that belongs to the team: a rebuild never overwrites it.
"""


def build(out: Path, reset_workspace: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    keep = {"workspace", ".env", ".venv", "logs", "reports"}
    for child in out.iterdir():
        if child.name in keep:
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()

    for name in TREES:
        (out / name).mkdir(parents=True, exist_ok=True)
        _copy_tree(ROOT / name, out / name)
    for rel in FILES:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, out / rel)

    edition = out / "webapp" / "edition.py"
    text = edition.read_text(encoding="utf-8")
    if EDITION_LINE not in text:
        raise SystemExit("webapp/edition.py no longer has the LITE line this script replaces.")
    edition.write_text(text.replace(EDITION_LINE, "LITE = True"), encoding="utf-8")

    for name in ("setup.ps1", "start.cmd"):
        body = (ROOT / name).read_text(encoding="utf-8")
        (out / name).write_text(body.replace("Start AutoPlay", "Start AutoPlay Lite")
                                .replace(" AutoPlay Setup ", " AutoPlay Lite Setup ")
                                .replace("Starts the AutoPlay web app", "Starts AutoPlay Lite"), encoding="utf-8")
    (out / "setup.cmd").write_bytes(SETUP_CMD.replace("\n", "\r\n").encode("utf-8"))
    (out / "README.md").write_text(readme(), encoding="utf-8")

    workspace = out / "workspace"
    if reset_workspace and workspace.exists():
        shutil.rmtree(workspace)
    if not workspace.exists():
        shutil.copytree(ROOT / "workspace", workspace, ignore=IGNORED)


def differences(built: Path, committed: Path) -> list[str]:
    """What differs between two Lite trees, ignoring the team's workspace and local files."""
    skip = {"workspace", ".env", ".venv", "logs", "reports", "__pycache__"}
    out: list[str] = []

    def walk(a: Path, b: Path, prefix: str) -> None:
        cmp = filecmp.dircmp(a, b, ignore=sorted(skip))
        out.extend(f"missing: {prefix}{n}" for n in cmp.left_only)
        out.extend(f"extra: {prefix}{n}" for n in cmp.right_only)
        out.extend(f"changed: {prefix}{n}" for n in (cmp.diff_files + cmp.funny_files))
        # dircmp compares by stat signature first; re-check content of the "same" ones
        for n in cmp.same_files:
            if (a / n).read_bytes() != (b / n).read_bytes():
                out.append(f"changed: {prefix}{n}")
        for n in cmp.common_dirs:
            walk(a / n, b / n, f"{prefix}{n}/")

    walk(built, committed, "")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 when the folder is out of date")
    ap.add_argument("--reset-workspace", action="store_true", help="replace the folder's workspace/ too")
    ap.add_argument("--out", type=Path, default=ROOT / OUT_NAME)
    args = ap.parse_args(argv)
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / OUT_NAME
            build(fresh)
            if not args.out.exists():
                print(f"{args.out} does not exist; run python tools/build_lite.py")
                return 1
            diff = differences(fresh, args.out)
        if diff:
            print("AutomationLite is out of date; run python tools/build_lite.py\n  " + "\n  ".join(diff[:30]))
            return 1
        print("AutomationLite is up to date.")
        return 0
    build(args.out, args.reset_workspace)
    print(f"Built {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
