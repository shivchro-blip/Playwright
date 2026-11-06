"""Quarantine: keep running a flaky test case but stop its failures from failing the run.

Data/quarantine.json in the workspace:
    { "tests": { "TC-APPLY-001": { "reason": "fails 1 run in 4 on the date picker", "since": "2026-11-05" } } }

A quarantined test case still runs. If it fails it is reported as xfailed (expected failure) and the run stays green;
if it passes it is reported as xpassed. A test case with a data set is matched by its ID without the [set] part.
Release a test case (remove it from the file) once it is fixed. Managed from the Generator or the History tab.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

SET_SUFFIX = re.compile(r"\[[^\]]*\]$")
FILE = ("Data", "quarantine.json")


def path_for(workspace: Path | str) -> Path:
    return Path(workspace).joinpath(*FILE)


def load(workspace: Path | str) -> dict[str, dict]:
    """test ID -> {reason, since}. A missing or unreadable file means nothing is quarantined."""
    try:
        data = json.loads(path_for(workspace).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    tests = data.get("tests") if isinstance(data, dict) else None
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in tests.items()} if isinstance(tests, dict) else {}


def base_id(test_id: str) -> str:
    return SET_SUFFIX.sub("", str(test_id or ""))


def save(workspace: Path | str, tests: dict[str, dict]) -> None:
    path = path_for(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"tests": dict(sorted(tests.items()))}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def add(workspace: Path | str, test_id: str, reason: str = "") -> None:
    tests = load(workspace)
    tests[base_id(test_id)] = {"reason": (reason or "").strip()[:200], "since": time.strftime("%Y-%m-%d")}
    save(workspace, tests)


def remove(workspace: Path | str, test_id: str) -> bool:
    tests = load(workspace)
    if base_id(test_id) not in tests:
        return False
    del tests[base_id(test_id)]
    save(workspace, tests)
    return True


def mark(items, workspace: Path | str) -> int:
    """Add the xfail marker to quarantined test items. Returns how many were marked."""
    quarantined = load(workspace)
    if not quarantined:
        return 0
    count = 0
    for item in items:
        callspec = getattr(item, "callspec", None)
        case = (callspec.params.get("test_case") if callspec else None) or {}
        info = quarantined.get(base_id(case.get("testCaseID", ""))) if isinstance(case, dict) else None
        if info is None:
            continue
        reason = "quarantined" + (f": {info['reason']}" if info.get("reason") else "")
        item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
        count += 1
    return count
