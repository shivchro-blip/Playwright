"""What a formData value means, in plain words: the action menu of the test case editor.

formData stores an action as a value ("click", null, {"type": "confirmtext"}, ...). The editor shows it as a
verb (Click, Generate a value, Verify text, ...) and writes the same JSON back, so files round-trip unchanged.
No UI imports here: the mapping is plain data and unit-tested.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

ACTIONS: dict[str, str] = {
    "fill": "Fill",
    "select": "Select option",
    "choose": "Choose option",
    "click": "Click",
    "dblclick": "Double-click",
    "hover": "Hover",
    "check": "Check",
    "setchecked": "Set checked",
    "kendodate": "Set date (Kendo)",
    "generate": "Generate a value",
    "verify": "Verify text",
    "key": "Press key",
    "wait": "Wait (seconds)",
    "custom": "Custom (JSON)",
}
SHORTHANDS = ("click", "dblclick", "hover", "check", "setchecked", "kendodate")  # the engine lowercases strings
CLICK_TYPES = {"button", "link", "checkbox", "radio", "table"}
HINTS = {
    "click": "Clicks the field.", "dblclick": "Double-clicks the field.", "hover": "Moves the pointer over it.",
    "check": "Ticks the checkbox.", "setchecked": "Sets the checkbox by script.",
    "kendodate": "Sets the date by script (Kendo date picker).",
    "generate": "Uses the field's autoGen rule on the page (or its default action).",
    "verify": "Checks the field's text against 'contains' on the page.",
}
KEY_NAMES = ("Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Backspace", "Delete", "Space")
_LOOP = re.compile(r"^\|(E|L)Loop\((.+)\)$")


@dataclass
class Row:
    """The storage form of one formData entry (what FormRow in the editor holds)."""
    key: str
    kind: str  # text | auto | number | json
    raw: str


def split_key(key: str) -> tuple[str, str]:
    """'next||' -> ('next', '||'); 'add|ELoop(3)' -> ('add', '|ELoop(3)')."""
    name, bar, rest = key.partition("|")
    return name, bar + rest


def loop_of(key: str) -> tuple[str, str]:
    """('ELoop' | 'LLoop' | '', count text) from a key's suffix."""
    m = _LOOP.match(split_key(key)[1])
    return (m.group(1) + "Loop", m.group(2).strip()) if m else ("", "")


def with_loop(key: str, kind: str, count: str) -> str:
    """The key with its repeat suffix replaced ('' kind removes it and any pipes)."""
    name = split_key(key)[0]
    return f"{name}|{kind}({count})" if kind and str(count).strip() else name


def _json(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def has_value_slot(cfg: dict | None) -> bool:
    """A field whose locator takes the answer from the test: ...button:text-is('{value}')."""
    return bool(cfg) and "{value}" in str(cfg.get("locator") or "")


def row_action(row: Row, cfg: dict | None = None) -> str:
    if row.kind == "auto":
        return "generate"
    if row.kind == "number":
        return "fill"
    if row.kind == "json":
        data = _json(row.raw)
        kind = str(data.get("type", "")).lower() if isinstance(data, dict) else ""
        return {"confirmtext": "verify", "keyboard": "key", "wait": "wait"}.get(kind, "custom")
    word = row.raw.strip().lower()
    if word in SHORTHANDS:
        return word
    if has_value_slot(cfg):
        return "choose"
    return "select" if cfg and cfg.get("type") == "select" else "fill"


def default_action(cfg: dict | None) -> str:
    """The action to start with for a field, from what it is."""
    if not cfg:
        return "fill"
    if has_value_slot(cfg):
        return "choose"
    kind = cfg.get("type")
    if kind in CLICK_TYPES:
        return "click"
    return "select" if kind == "select" else "fill"


def action_options(cfg: dict | None) -> dict[str, str]:
    """The menu for a field: only what makes sense for it (all of it for an unknown field)."""
    skip: set[str] = set()
    if cfg:
        keep = "choose" if has_value_slot(cfg) else ("select" if cfg.get("type") == "select" else "fill")
        skip = {"fill", "select", "choose"} - {keep}
    return {k: v for k, v in ACTIONS.items() if k not in skip}


def apply_action(row: Row, action: str, cfg: dict | None = None) -> None:
    """Switch a row to another action, keeping what can be kept (a typed value survives fill <-> choose)."""
    before = row_action(row, cfg)
    if action in ("fill", "select", "choose"):
        keep = row.kind == "text" and before in ("fill", "select", "choose")
        row.kind, row.raw = ("number" if row.kind == "number" and action == "fill" else "text"), \
            (row.raw if keep or row.kind == "number" else "")
    elif action in SHORTHANDS:
        row.kind, row.raw = "text", action
    elif action == "generate":
        row.kind, row.raw = "auto", ""
    elif action == "verify":
        row.kind, row.raw = "json", json.dumps({"type": "confirmtext"})
    elif action == "key":
        row.kind, row.raw = "json", json.dumps({"type": "keyboard", "value": "Enter"})
    elif action == "wait":
        row.kind, row.raw = "json", json.dumps({"type": "wait", "value": "2"})
    elif action == "custom":
        if row.kind != "json":
            row.raw = json.dumps({"type": ""})
        row.kind = "json"


def json_value(row: Row) -> str:
    """The editable 'value' of a key / wait action."""
    data = _json(row.raw)
    return str(data.get("value", "")) if isinstance(data, dict) else ""


def set_json_value(row: Row, value: str) -> None:
    data = _json(row.raw)
    data = data if isinstance(data, dict) else {}
    row.raw = json.dumps({**data, "value": value})


def ref_value(kind: str, name: str) -> str:
    """ENV.NAME / DICT.name, the engine's references to .env values and to values saved earlier in the test."""
    return f"{kind}.{name.strip()}" if name.strip() else ""
