"""Steps v2: plain action lists that mean exactly what the classic steps mean, and common action parameters."""
import json
from pathlib import Path

import pytest

from utils.steps_v2 import (StepError, apply_params, edit_form, expand_common, form_data, is_v2, params_used, to_v1,
                            to_v2)

ROOT = Path(__file__).resolve().parents[2]

V2 = {"screen": "Login", "index": 1, "do": [
    {"field": "username", "value": "ann"}, {"field": "signIn", "click": True},
    {"field": "username", "value": "again"}, {"field": "username", "press": "Enter"},
    {"field": "spinner", "wait": 2}, {"field": "agree", "check": True},
    {"field": "dob", "use": {"type": "muidate", "value": "1990-01-01"}}]}
V1 = {"screen": "Login", "index": 1, "formData": {
    "username": "ann", "signIn": "click", "username|": "again", "username||": {"type": "keyboard", "value": "Enter"},
    "spinner": {"type": "wait", "value": "2"}, "agree": "check",
    "dob": {"type": "muidate", "value": "1990-01-01"}}}


def test_v2_means_the_classic_step():
    assert to_v1(V2) == V1
    assert to_v2(V1) == V2
    assert is_v2(V2) and not is_v2(V1)
    assert form_data(V2) == V1["formData"] == form_data(V1)
    assert form_data({"commonAction": "x"}) == {} and to_v1(V1) is V1 and to_v2({"testCase": "T"}) == {"testCase": "T"}


def test_lossless_for_odd_values():
    odd = {"screen": "S", "formData": {"a": {"type": "wait", "value": "02"}, "b": {"type": "keyboard"},
                                       "c": None, "d": True, "e": 3, "f": ["x"], "g": "click"}}
    assert to_v1(to_v2(odd)) == odd


def test_mistakes_are_named():
    with pytest.raises(StepError, match="needs a field"):
        to_v1({"screen": "S", "do": [{"value": "x"}]})
    with pytest.raises(StepError, match="say one of"):
        to_v1({"screen": "S", "do": [{"field": "a", "value": "x", "click": True}]})
    assert form_data({"screen": "S", "do": [{"value": "x"}]}) == {}  # readers never crash on a bad step


def test_every_step_in_the_repository_round_trips():
    checked = 0
    for path in list((ROOT / "workspace" / "Tests").rglob("*.json")) + \
            list((ROOT / "tests" / "e2e" / "fixtures" / "workspace" / "Tests").rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        cases = data.get("testCases", []) if isinstance(data, dict) else []
        actions = (data.get("commonActions") or {}).values() if isinstance(data, dict) else []
        for owner in list(cases) + list(actions):
            for step in owner.get("steps") or [] if isinstance(owner, dict) else []:
                if isinstance(step, dict) and isinstance(step.get("formData"), dict):
                    assert to_v1(to_v2(step)) == step, (path, step)
                    checked += 1
    assert checked > 5


def test_edit_form_keeps_the_format():
    renamed = edit_form(V2, lambda form: {("user" if k.rstrip("|") == "username" else k.rstrip("|")) +
                                          "|" * (len(k) - len(k.rstrip("|"))): v for k, v in form.items()})
    assert is_v2(renamed) and [i["field"] for i in renamed["do"]][:3] == ["user", "signIn", "user"]
    classic = edit_form(V1, lambda form: {k: v for k, v in form.items() if k != "agree"})
    assert "agree" not in classic["formData"] and not is_v2(classic)


def test_common_action_parameters():
    action = {"params": {"user": "demo", "remember": False},
              "steps": [{"screen": "Login", "do": [{"field": "username", "value": "{{user}}"},
                                                    {"field": "remember", "value": "{{remember}}"},
                                                    {"field": "note", "value": "hello {{user}}, {{unknown}}"}]}]}
    steps = expand_common(action, {"commonAction": "login", "with": {"user": "ann"}})
    assert steps == [{"screen": "Login", "formData": {"username": "ann", "remember": False,
                                                      "note": "hello ann, {{unknown}}"}}]
    again = expand_common(action, {"commonAction": "login"})
    assert again[0]["formData"]["username"] == "demo"
    assert action["steps"][0]["do"][0]["value"] == "{{user}}"  # the action itself is never changed
    assert params_used(action["steps"]) == {"user", "remember", "unknown"}
    assert apply_params({"a": ["{{x}}", 1]}, {"x": {"deep": True}}) == {"a": [{"deep": True}, 1]}
