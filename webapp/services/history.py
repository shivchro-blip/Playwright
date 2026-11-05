"""Edit history: every save of a page, test file, data set or common-actions file keeps the previous version.

Versions live in <workspace>/.history/<path relative to the workspace>/<timestamp>.json (git-ignored, never sent to
runners). The newest KEEP versions of a file are kept. Restoring writes an old version back through the normal save
(which keeps the version it replaces), so a restore can be undone too.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_DIR = ".history"
KEEP = 40
ROOT_FOLDERS = ("Data", "Pages", "Tests")
VERSION_RE = re.compile(r"^\d{8}-\d{6}-\d{3}\.json$")


@dataclass
class Version:
    id: str  # file name, also the sort key (newest = largest)
    at: str  # "2026-11-05 14:31:02"
    size: int


def workspace_root(path: Path) -> Path | None:
    """The workspace a file belongs to: the parent of its Data, Pages or Tests ancestor."""
    path = Path(path).resolve()
    for parent in path.parents:
        if parent.name in ROOT_FOLDERS:
            siblings = [f for f in ROOT_FOLDERS if f != parent.name and (parent.parent / f).is_dir()]
            if siblings or (parent.parent / HISTORY_DIR).is_dir():  # a folder named Data inside Tests is not it
                return parent.parent
    return None


def folder_for(path: Path) -> Path | None:
    root = workspace_root(path)
    if root is None:
        return None
    return root / HISTORY_DIR / Path(path).resolve().relative_to(root)


def snapshot(path: Path, previous_text: str) -> None:
    """Keep `previous_text` (what is about to be overwritten). Never raises: a save must not fail over history."""
    try:
        folder = folder_for(path)
        if folder is None or not previous_text.strip():
            return
        folder.mkdir(parents=True, exist_ok=True)
        existing = sorted(p for p in folder.iterdir() if VERSION_RE.match(p.name))
        if existing and existing[-1].read_text(encoding="utf-8") == previous_text:
            return  # nothing changed since the last kept version
        now = time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int((now % 1) * 1000):03d}"
        (folder / f"{stamp}.json").write_text(previous_text, encoding="utf-8")
        for old in existing[: max(0, len(existing) + 1 - KEEP)]:
            old.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not keep a version of %s", path, exc_info=True)


def versions(path: Path) -> list[Version]:
    """Kept versions of the file, newest first."""
    folder = folder_for(path)
    if folder is None or not folder.is_dir():
        return []
    out = []
    for p in sorted((p for p in folder.iterdir() if VERSION_RE.match(p.name)), reverse=True):
        s = p.name
        out.append(Version(s, f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]}:{s[13:15]}", p.stat().st_size))
    return out


def read_version(path: Path, version_id: str) -> str:
    folder = folder_for(path)
    if folder is None or not VERSION_RE.match(version_id or ""):
        raise ValueError("No such version.")
    target = folder / version_id
    if not target.is_file():
        raise ValueError("That version is gone.")
    return target.read_text(encoding="utf-8")


def diff(old: str, new: str, old_label: str = "version", new_label: str = "current") -> list[str]:
    """Unified diff lines (no trailing newlines) between two texts."""
    return [line.rstrip("\n") for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), old_label, new_label, lineterm="", n=3)]
