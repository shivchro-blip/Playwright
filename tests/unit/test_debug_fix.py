"""Fixes from a paused run: add a field to a step, put a locator on a page's field, list what changed since."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from utils.steps_v2 import is_v2
from webapp.services import debug_fix
from webapp.services.debug_fix import DebugFixError, insert_field
from webapp.services.json_files import ConflictError
from webapp.services.pom_service import PomService
from webapp.services.test_case_service import TestCaseService


def write(root: Path, rel: str, data) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def workspace(tmp_path: Path):
    write(tmp_path, "Pages/S/Form.json", {"path": "/f", "fields": {
        "name": {"locator": "#name", "type": "text", "autoGen": "firstName"}, "zip": {"locator": "#zip"}}})
    write(tmp_path, "Tests/S/MasterRegressionSuite/a.json", {"testCases": [
        {"testCaseID": "TC-1", "tags": ["t"], "steps": [
            {"screen": "Form", "formData": {"name": "Ann", "zip": "1"}},
            {"screen": "Form", "formData": {"zip": "2"}}]}]})
    pom = PomService(tmp_path, tmp_path)
    return pom, TestCaseService(tmp_path, pom)


def test_a_field_goes_where_asked_in_a_step_and_a_repeat_gets_a_pipe():
    step = {"screen": "Form", "formData": {"name": "Ann", "zip": "1"}}
    assert list(insert_field(step, "city", "fill", "Austin", 1)["formData"].items()) == \
        [("name", "Ann"), ("city", "Austin"), ("zip", "1")]
    assert list(insert_field(step, "zip", "click")["formData"]) == ["name", "zip", "zip|"]  # at the end, repeated
    assert insert_field(step, "ok", "check", position=0)["formData"] == {"ok": "check", "name": "Ann", "zip": "1"}
    assert insert_field(step, "k", "press", "Tab", 99)["formData"]["k"] == {"type": "keyboard", "value": "Tab"}
    assert step["formData"] == {"name": "Ann", "zip": "1"}  # the original is not touched
    assert insert_field({"screen": "Form"}, "a", "fill", "x")["formData"] == {"a": "x"}  # a step with no fields yet


def test_an_explicit_step_stays_explicit_and_only_a_page_step_takes_a_field():
    v2 = {"screen": "Form", "do": [{"field": "name", "value": "Ann"}]}
    assert is_v2(v2)
    out = insert_field(v2, "zip", "fill", "9")
    assert is_v2(out) and {"field": "zip", "value": "9"} in out["do"]
    with pytest.raises(DebugFixError, match="on a page"):
        insert_field({"commonAction": "login"}, "a", "fill")
    with pytest.raises(DebugFixError, match="Unknown action"):
        insert_field({"screen": "Form"}, "a", "teleport")


def test_the_field_is_added_to_the_saved_test_case_at_the_failed_place(tmp_path):
    _pom, tests = workspace(tmp_path)
    file = debug_fix.add_field_to_test(tests, "S", "TC-1", 1, "city", "fill", "Austin", position=1)
    case, _ = tests.get("S", file, 0)
    assert list(case["steps"][0]["formData"].items()) == [("name", "Ann"), ("city", "Austin"), ("zip", "1")]
    assert case["steps"][1]["formData"] == {"zip": "2"}  # the other step is as it was
    with pytest.raises(DebugFixError, match="no step 3"):
        debug_fix.add_field_to_test(tests, "S", "TC-1", 3, "a", "fill")
    with pytest.raises(DebugFixError, match="not found"):
        debug_fix.add_field_to_test(tests, "S", "TC-NOPE", 1, "a", "fill")


def test_a_locator_goes_on_the_field_and_its_other_settings_stay(tmp_path):
    pom, _tests = workspace(tmp_path)
    rel = debug_fix.apply_locator(pom, "S", "Form", "name", {"role": "textbox", "name": "Name", "index": 1})
    data, _ = pom.load("S", rel)
    assert data["fields"]["name"] == {"type": "text", "autoGen": "firstName", "role": "textbox", "name": "Name",
                                      "index": 1}  # the old locator is gone, type and autoGen are kept
    debug_fix.apply_locator(pom, "S", "Form", "city", {"locator": "#city"}, "text")  # a field the page lacked
    data, _ = pom.load("S", rel)
    assert data["fields"]["city"] == {"type": "text", "locator": "#city"}
    assert data["fields"]["zip"] == {"locator": "#zip"}
    with pytest.raises(DebugFixError, match="not found"):
        debug_fix.apply_locator(pom, "S", "Nope", "a", {"locator": "#a"})


def test_a_file_that_changed_while_the_fix_was_made_is_refused_not_overwritten(tmp_path, monkeypatch):
    pom, _tests = workspace(tmp_path)
    real = pom.load

    def load_then_change(system, rel):
        result = real(system, rel)
        data = json.loads((tmp_path / "Pages/S/Form.json").read_text(encoding="utf-8"))
        data["path"] = "/someone-else"  # a teammate saved in between
        write(tmp_path, "Pages/S/Form.json", data)
        return result

    monkeypatch.setattr(pom, "load", load_then_change)
    with pytest.raises(ConflictError):
        debug_fix.apply_locator(pom, "S", "Form", "zip", {"locator": "#z2"})
    assert json.loads((tmp_path / "Pages/S/Form.json").read_text())["path"] == "/someone-else"


def test_files_saved_since_the_pause_are_listed_newest_first(tmp_path):
    pom, _tests = workspace(tmp_path)
    old = time.time() - 100
    for path in tmp_path.rglob("*.json"):
        os.utime(path, (old, old))
    since = time.time() - 10
    assert debug_fix.changed_since(tmp_path, "S", since) == []
    write(tmp_path, "Data/systemData.json", {"systems": {}})
    time.sleep(0.02)
    write(tmp_path, "Pages/S/Form.json", {"fields": {}})
    write(tmp_path, "Pages/OTHER/X.json", {"fields": {}})  # another system's page is not this run's business
    found = debug_fix.changed_since(tmp_path, "S", since)
    assert [path for path, _when in found] == ["Pages/S/Form.json", "Data/systemData.json"]
