"""Safe JSON file IO for the workspace editors.

- Paths are always relative to a base folder; '..', absolute paths and odd names are rejected.
- Writes are atomic and keep the file's existing indentation.
- Every read returns a content hash; writes pass it back so a file changed by someone
  else (another tab, git pull, a teammate on the shared server) is not silently overwritten.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[\w .()\-]+$")
DEFAULT_INDENT = "  "


class PathError(ValueError):
    """Path is outside the allowed folder or has an invalid name."""


class ConflictError(RuntimeError):
    """File changed on disk since it was loaded."""


def safe_path(base: Path, rel: str) -> Path:
    rel = str(rel or "").replace("\\", "/").strip().strip("/")
    parts = rel.split("/")
    if not rel or not rel.lower().endswith(".json"):
        raise PathError(f"Not a .json path: {rel!r}")
    if any(p in ("", ".", "..") or not NAME_RE.match(p) for p in parts):
        raise PathError(f"Invalid path: {rel!r}")
    base = Path(base).resolve()
    path = (base / Path(*parts)).resolve()
    if base not in path.parents:
        raise PathError(f"Path escapes {base}: {rel!r}")
    return path


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_indent(text: str) -> str:
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and len(stripped) != len(line):
            return line[: len(line) - len(stripped)]
    return DEFAULT_INDENT


def read_json(path: Path) -> tuple[Any, str, str]:
    """(data, hash, indent). Raises OSError / ValueError on unreadable files."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return json.loads(text), content_hash(text), detect_indent(text)


def current_hash(path: Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    return content_hash(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any, expected_hash: str | None, indent: str | None = None) -> str:
    """Write atomically. expected_hash: hash from the last read, or None for a new file.
    Returns the new hash."""
    path = Path(path)
    on_disk = current_hash(path)
    if expected_hash is None and on_disk is not None:
        raise ConflictError(f"{path.name} already exists.")
    if expected_hash is not None and on_disk != expected_hash:
        raise ConflictError(f"{path.name} was changed on disk since you opened it. Reload it first.")
    if indent is None:
        indent = detect_indent(path.read_text(encoding="utf-8-sig")) if on_disk else DEFAULT_INDENT

    text = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return content_hash(text)
