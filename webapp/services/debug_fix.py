"""Fixes made from a paused run: put a locator on a page's field, or add a field to a step of the test case.

Both change the files the run reads again when it resumes (utils/debug_pause.py), through the same services the
Generator saves with, so a file that changed on disk since it was read is refused, not overwritten."""
from __future__ import annotations

import copy

from utils.steps_v2 import is_v2, to_v1, to_v2
from webapp.services.locator_suggest import KEYS as LOCATOR_KEYS

ACTIONS = ("fill", "select", "click", "check", "press")


class DebugFixError(Exception):
    pass


def encode_action(action: str, value: str = "") -> object:
    """What formData stores for an action on a field: a value to type or choose, a word, or a key press."""
    if action in ("fill", "select"):
        return value
    if action in ("click", "check"):
        return action
    if action == "press":
        return {"type": "keyboard", "value": value or "Enter"}
    raise DebugFixError(f"Unknown action {action!r}.")


def unique_key(form: dict, name: str) -> str:
    """The key to use for a field in a step: the name, with | added for each earlier use of it in the same step."""
    key = name
    while key in form:
        key += "|"
    return key


def insert_field(step: dict, name: str, action: str, value: str = "", position: int | None = None) -> dict:
    """The step with a field added at `position` among its fields (the end when None). A step written as an explicit
    list of actions ("do") stays one. Raises DebugFixError for a step with no fields to add to (not a page step)."""
    if "screen" not in step and not is_v2(step):
        raise DebugFixError("Only a step on a page can have a field added.")
    v2 = is_v2(step)
    classic = to_v1(copy.deepcopy(step)) if v2 else copy.deepcopy(step)
    form = classic.get("formData")
    form = dict(form) if isinstance(form, dict) else {}
    items = list(form.items())
    at = len(items) if position is None else max(0, min(int(position), len(items)))
    items.insert(at, (unique_key(form, name), encode_action(action, value)))
    classic["formData"] = dict(items)
    return to_v2(classic) if v2 else classic


def add_field_to_test(tests, system: str, test_id: str, step_number: int, name: str, action: str, value: str = "",
                      position: int | None = None) -> str:
    """Add a field to step `step_number` (1-based, in the test case) of a test case and save it. Returns the file."""
    refs, _errors = tests.list_tests(system)
    ref = next((r for r in refs if r.test_id == test_id), None)
    if ref is None:
        raise DebugFixError(f"Test case {test_id} was not found in {system}.")
    case, sha = tests.get(system, ref.file, ref.index)
    steps = case.get("steps") or []
    if not 1 <= step_number <= len(steps):
        raise DebugFixError(f"{test_id} has no step {step_number}.")
    steps[step_number - 1] = insert_field(steps[step_number - 1], name, action, value, position)
    tests.save(system, ref.file, ref.index, case, sha)
    return ref.file


def apply_locator(pom, system: str, screen: str, field: str, cfg: dict, field_type: str = "") -> str:
    """Put a locator on a page's field (a new field when the page has none of that name) and save the page. Keeps the
    field's other settings (type, autoGen, ...). Returns the page's file."""
    rel = pom.screen_index(system).get(screen)
    if not rel:
        raise DebugFixError(f"Page {screen} was not found in Pages/{system}.")
    data, sha = pom.load(system, rel)
    fields = data.setdefault("fields", {})
    old = fields.get(field) if isinstance(fields.get(field), dict) else {}
    keep = {k: v for k, v in old.items() if k not in LOCATOR_KEYS}
    if "type" not in keep and field_type:
        keep["type"] = field_type
    fields[field] = {**keep, **{k: v for k, v in cfg.items() if k in LOCATOR_KEYS}}
    pom.save(system, rel, data, sha)
    return rel


def changed_since(workspace, system: str, since: float, limit: int = 8) -> list[tuple[str, float]]:
    """Files of the workspace saved after `since` (a time.time()): the system's pages, every test file and the Data
    files, newest first, as (path relative to the workspace, when). What a paused run will read again."""
    from pathlib import Path
    root = Path(workspace)
    found: list[tuple[str, float]] = []
    for folder in (root / "Pages" / system, root / "Tests", root / "Data"):
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.json"):
            try:
                when = path.stat().st_mtime
            except OSError:
                continue
            if when > since:
                found.append((path.relative_to(root).as_posix(), when))
    return sorted(found, key=lambda item: -item[1])[:limit]
