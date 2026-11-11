"""Clear all: delete what previous runs left behind, and nothing else.

What a run leaves, and where (see RunManager._execute: pytest runs with the repo root as its working directory):

    <runs dir>/<run id>/            console.log, run.json, allure-results, allure-report, report.html, screenshots,
                                    traces, saved states (checkpoints): one folder per run
    <workspace>/logs/automation.log*    the suite's rotating log file
    <repo>/logs/context_log_*.json      the suite's per-test context logs
    <repo>/reports/videos, downloads, screenshots, traces   recordings, files the site under test let the browser
                                    download, and what the framework saves when a test fails

Never touched: source, .env and settings, credentials and the secret store, systemData.json, pages, tests, data
sets and the user's input files (Data/Documents), the edit history of workspace files, users and API tokens, the
audit trail, schedules, monitors, release packs, quarantine and heal decisions, and anything an active run uses.
A run that is queued or running keeps its folder, and the shared locations above are left alone until no run is
active, because a running test writes to them.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

RUN_FOLDER_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")  # RunManager.start names a run folder this way
RUN_MARKERS = ("run.json", "console.log", "allure-results")  # what a folder of ours has; a stranger's does not
Failure = tuple[str, str]  # (path, why)


def tree_size(path: Path) -> int:
    """Bytes under path (a file, or a folder without following links); what cannot be read counts as 0."""
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def remove_tree(path: Path) -> list[Failure]:
    """Delete a file, a link (not what it points to) or a folder; returns what could not be deleted and why."""
    failures: list[Failure] = []

    def note(_func, failed, error) -> None:
        failures.append((str(failed), str(getattr(error, "strerror", None) or error)))

    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=lambda f, p, e: note(f, p, e))
            else:  # pragma: no cover - the app is tested on newer Pythons
                shutil.rmtree(path, onerror=lambda f, p, i: note(f, p, i[1]))
    except OSError as e:
        failures.append((str(path), str(e.strerror or e)))
    return failures


def looks_like_run_folder(path: Path) -> bool:
    return bool(RUN_FOLDER_RE.match(path.name)) and path.is_dir() and not path.is_symlink() \
        and any((path / marker).exists() for marker in RUN_MARKERS)


def shared_targets(repo_root: Path, workspace: Path) -> list[tuple[str, Path]]:
    """(what it is, path) for the files and folders runs write outside their own folder, that exist now."""
    found: list[tuple[str, Path]] = []
    for label, folder, pattern in (("Suite log", Path(workspace) / "logs", "automation.log*"),
                                   ("Test context log", Path(repo_root) / "logs", "context_log_*.json")):
        if folder.is_dir():
            found += [(label, p) for p in sorted(folder.glob(pattern)) if p.is_file() or p.is_symlink()]
    for label, name in (("Recorded videos", "videos"), ("Downloaded files", "downloads"),
                        ("Failure screenshots", "screenshots"), ("Failure traces", "traces")):
        folder = Path(repo_root) / "reports" / name
        if folder.is_dir() or folder.is_symlink():
            found.append((label, folder))
    return found


@dataclass
class Preview:
    finished: int = 0  # runs that will be deleted
    active: int = 0  # runs in progress or waiting: kept
    orphans: int = 0  # leftover run folders no run owns
    run_bytes: int = 0
    shared: list[tuple[str, str, int]] = field(default_factory=list)  # (what, path, bytes)
    shared_skipped: bool = False  # a run is active, so the shared locations stay as they are

    @property
    def shared_bytes(self) -> int:
        return sum(size for _what, _path, size in self.shared)

    @property
    def total_bytes(self) -> int:
        return self.run_bytes + (0 if self.shared_skipped else self.shared_bytes)

    @property
    def empty(self) -> bool:
        return not (self.finished or self.orphans) and (self.shared_skipped or not self.shared)


@dataclass
class Report:
    runs: int = 0
    orphans: int = 0
    shared: int = 0
    bytes_freed: int = 0
    kept_active: int = 0
    shared_skipped: bool = False
    failures: list[Failure] = field(default_factory=list)

    @property
    def deleted(self) -> int:
        return self.runs + self.orphans + self.shared


def preview(runs, repo_root: Path, workspace: Path) -> Preview:
    """What clear_all would delete right now (reads sizes; deletes nothing). `runs` is the RunManager."""
    p = Preview()
    owned = {r.id for r in runs.list()}
    for run in runs.list():
        if run.active:
            p.active += 1
        else:
            p.finished += 1
            p.run_bytes += tree_size(run.dir)
    for folder in sorted(runs.runs_dir.iterdir()) if runs.runs_dir.is_dir() else []:
        if folder.name not in owned and looks_like_run_folder(folder):
            p.orphans += 1
            p.run_bytes += tree_size(folder)
    p.shared_skipped = p.active > 0
    p.shared = [(what, str(path), tree_size(path)) for what, path in shared_targets(repo_root, workspace)]
    return p


def clear_all(runs, repo_root: Path, workspace: Path) -> Report:
    """Delete every finished run (its folder, its entry in the history and the results index), leftover run folders,
    and, while no run is active, the shared files runs write. Failures are collected, never raised."""
    report = Report()
    removed = runs.purge_finished()
    report.runs, report.orphans = len(removed.runs), len(removed.orphans)
    report.bytes_freed += removed.bytes_freed
    report.failures += removed.failures
    report.kept_active = sum(1 for r in runs.list() if r.active)
    if report.kept_active:  # a running test writes to the shared locations
        report.shared_skipped = True
        return report
    for _what, path in shared_targets(repo_root, workspace):
        size = tree_size(path)
        failures = remove_tree(path)
        report.failures += failures
        if not path.exists() and not path.is_symlink():
            report.shared += 1
            report.bytes_freed += size
    return report
