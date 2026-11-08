"""Steps v2: what a screen step does, said plainly, and parameters for common actions.

A classic screen step says what to do through hidden rules: the order of the keys in "formData" is the order of
the actions, a field used twice is written "name|" (and "name||"), and the meaning of a value depends on its shape
("click", "check", {"type": "keyboard", ...}). Steps v2 lists the actions instead:

    {"screen": "Login", "do": [{"field": "username", "value": "ann"},
                               {"field": "password", "value": "ENV.PASSWORD"},
                               {"field": "signIn", "click": true},
                               {"field": "username", "value": "again"},   # the same field again: no "|"
                               {"field": "search", "press": "Enter"},
                               {"field": "spinner", "wait": 2},
                               {"field": "agree", "check": true},
                               {"field": "dob", "use": {"type": "muidate", "value": "1990-01-01"}}]}

`value` is typed or chosen (by the field's type, as before), `click`/`check` act, `press` presses a key, `wait`
waits, and `use` passes any classic value through untouched, so every classic step converts to v2 and back to
exactly the same thing (to_v2 / to_v1). The runner turns a v2 step into the classic one just before running it, so
both formats work everywhere, side by side, even in one test case.

Common actions take parameters: a step {"commonAction": "login", "with": {"user": "ann"}} fills "{{user}}" in the
action's steps; an action may give defaults: {"params": {"user": "demo"}, "steps": [...]}. Each use gets its own
copy of the steps, so one use's values (or override) never leak into the next.
"""
from __future__ import annotations

import copy
import re

VERBS = ("value", "click", "check", "press", "wait", "use")
PARAM_RE = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)\s*\}\}")


class StepError(ValueError):
    pass


def is_v2(step) -> bool:
    return isinstance(step, dict) and isinstance(step.get("do"), list)


def _item_to_value(item: dict, n: int):
    if not isinstance(item, dict) or not str(item.get("field") or "").strip():
        raise StepError(f"Action {n}: needs a field.")
    verbs = [v for v in VERBS if v in item]
    if len(verbs) != 1:
        raise StepError(f"Action {n} ({item.get('field')}): say one of {', '.join(VERBS)}.")
    verb = verbs[0]
    value = item[verb]
    if verb == "value":
        return value
    if verb == "click":
        return "click"
    if verb == "check":
        return "check"
    if verb == "press":
        return {"type": "keyboard", "value": str(value or "Enter")}
    if verb == "wait":
        return {"type": "wait", "value": str(value)}
    return value  # use: a classic value, as is


def to_v1(step: dict) -> dict:
    """The classic step a v2 step means (a classic step is returned as it is)."""
    if not is_v2(step):
        return step
    form: dict = {}
    for n, item in enumerate(step["do"], 1):
        key = str(item.get("field") if isinstance(item, dict) else "").strip()
        value = _item_to_value(item, n)
        while key in form:
            key += "|"
        form[key] = value
    out = {k: v for k, v in step.items() if k != "do"}
    out["formData"] = form
    return out


def _value_to_item(field: str, value) -> dict:
    if value == "click":
        return {"field": field, "click": True}
    if value == "check":
        return {"field": field, "check": True}
    if isinstance(value, dict) and set(value) == {"type", "value"}:  # only exact shapes: the round trip is lossless
        if value["type"] == "keyboard" and isinstance(value["value"], str) and value["value"]:
            return {"field": field, "press": value["value"]}
        if value["type"] == "wait" and isinstance(value["value"], str) and str(_number(value["value"])) == \
                value["value"]:
            return {"field": field, "wait": _number(value["value"])}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return {"field": field, "value": value}
    return {"field": field, "use": value}


def _number(text):
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return int(number) if number.is_integer() and "." not in str(text) else number


def to_v2(step: dict) -> dict:
    """The v2 form of a classic screen step (other steps, and v2 steps, are returned as they are)."""
    if is_v2(step) or not isinstance(step, dict) or not isinstance(step.get("formData"), dict):
        return step
    do = [_value_to_item(str(key).rstrip("|"), value) for key, value in step["formData"].items()]
    out = {k: v for k, v in step.items() if k != "formData"}
    out["do"] = do
    return out


def form_data(step) -> dict:
    """The classic formData of any screen step, v1 or v2 ({} when it has none) — for code that reads fields."""
    if is_v2(step):
        try:
            return to_v1(step).get("formData") or {}
        except StepError:
            return {}
    form = step.get("formData") if isinstance(step, dict) else None
    return form if isinstance(form, dict) else {}


def edit_form(step: dict, change) -> dict:
    """Apply `change(formData) -> formData` to a step in its own format (a v2 step stays v2)."""
    if is_v2(step):
        v1 = to_v1(step)
        return to_v2({**v1, "formData": change(dict(v1["formData"]))})
    if isinstance(step.get("formData"), dict):
        return {**step, "formData": change(dict(step["formData"]))}
    return step


# ───── common action parameters ─────

def apply_params(value, params: dict):
    """A deep copy of value with "{{name}}" replaced by params[name] (a whole-string "{{name}}" keeps the param's
    type). Unknown names are left as they are, so a mistake shows in the run instead of disappearing."""
    if isinstance(value, str):
        whole = PARAM_RE.fullmatch(value.strip())
        if whole and whole.group(1) in params:
            return copy.deepcopy(params[whole.group(1)])
        return PARAM_RE.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), value)
    if isinstance(value, list):
        return [apply_params(v, params) for v in value]
    if isinstance(value, dict):
        return {k: apply_params(v, params) for k, v in value.items()}
    return value


def expand_common(action: dict, call: dict) -> list[dict]:
    """The steps of a common action for one call: its own copy, with the call's parameters (over the action's
    defaults) filled in, as classic steps."""
    params = {**(action.get("params") or {}), **(call.get("with") or {})}
    return [to_v1(s) for s in apply_params(action.get("steps") or [], params)]


def params_used(steps) -> set[str]:
    """The parameter names a list of steps refers to."""
    found: set[str] = set()

    def walk(value):
        if isinstance(value, str):
            found.update(PARAM_RE.findall(value))
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
    walk(steps)
    return found
