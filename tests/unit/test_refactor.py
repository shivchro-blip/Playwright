"""Usage, rename, merge and tidy: they change a page and every test that uses it, all or nothing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from webapp.services.json_files import ConflictError
from webapp.services.pom_service import PomService
from webapp.services.refactor import Refactor, RefactorError, apply_plan
from webapp.services.test_case_service import TestCaseService as CaseService
from webapp.services.tidy import analyze, build_plan
from webapp.services.usage import UsageIndex

SAVE = {"locator": "button:has-text('Save & Continue')", "index": "0", "type": "button", "autoGen": "click"}
PAGE = {"path": "/x", "fields": {
    "Name": {"locator": "#name", "type": "text", "autoGen": "name"},
    "Save": dict(SAVE), "Save1": dict(SAVE), "Save2": dict(SAVE),
    "Old": {"locator": "#old", "type": "text"},
    "Principal": {"locator": ".p", "index": "0", "type": "button"},
    "Principal1": {"locator": ".p", "index": "1", "type": "button"},
}}


def w(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture
def ws(tmp_path: Path):
    w(tmp_path / "Pages" / "SYS" / "Form.json", PAGE)
    w(tmp_path / "Pages" / "SYS" / "Other.json", {"fields": {"Name": {"locator": "#n", "type": "text"}}})
    w(tmp_path / "Tests" / "SYS" / "MasterRegressionSuite" / "T.json", {"testCases": [
        {"testCaseID": "TC-1", "system": "SYS", "steps": [
            {"screen": "Form", "formData": {"Name": "Ann", "Save": "click", "Save1": "click", "Save2": None}},
            {"screen": "Other", "formData": {"Name": "x"}},
            {"commonAction": "Fill", "override": "Form|Name|Zed"}]},
        {"testCaseID": "TC-2", "system": "SYS", "steps": [{"screen": "Form", "formData": {"Save|": "click"}}]},
        {"testCaseID": "TC-3", "system": "ELSE", "steps": [{"screen": "Form", "formData": {"Old": "x"}}]},
    ]})
    w(tmp_path / "Tests" / "com" / "c.json", {"commonActions": {"Fill": {"steps": [
        {"screen": "Form", "formData": {"Name": "Common"}}]}}})
    pom = PomService(tmp_path, tmp_path)
    return tmp_path, pom, CaseService(tmp_path, pom)


def load(root: Path, *parts: str) -> dict:
    return json.loads(root.joinpath(*parts).read_text(encoding="utf-8"))


def test_usage_counts_tests_and_common_actions_of_the_system(ws):
    _, _, tests = ws
    usage = UsageIndex(tests).scan("SYS")
    assert usage.page_count("Form") == 3 and usage.page_count("Other") == 1  # TC-1, TC-2, common Fill
    assert {u.owner for u in usage.field_uses("Form", "Name")} == {"TC-1", "Fill"}  # + the override on TC-1
    assert usage.field_uses("Form", "Old") == []  # TC-3 runs in system ELSE
    assert usage.field_uses("Form", "Save1")[0].index == 0


def test_rename_page_updates_steps_overrides_and_the_file(ws):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.rename_page("SYS", "Form", "Application")
    plan = r.plan()
    apply_plan(plan)
    assert not (root / "Pages" / "SYS" / "Form.json").exists() and (root / "Pages" / "SYS" / "Application.json").exists()
    cases = load(root, "Tests", "SYS", "MasterRegressionSuite", "T.json")["testCases"]
    assert cases[0]["steps"][0]["screen"] == "Application" and cases[0]["steps"][1]["screen"] == "Other"
    assert cases[0]["steps"][2]["override"] == "Application|Name|Zed"
    assert cases[2]["steps"][0]["screen"] == "Form"  # another system's test, untouched
    assert load(root, "Tests", "com", "c.json")["commonActions"]["Fill"]["steps"][0]["screen"] == "Application"


def test_rename_field_keeps_order_pipes_and_only_touches_that_page(ws):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.rename_field("SYS", "Form", "Name", "FullName")
    apply_plan(r.plan())
    page = load(root, "Pages", "SYS", "Form.json")
    assert list(page["fields"])[:2] == ["FullName", "Save"]
    cases = load(root, "Tests", "SYS", "MasterRegressionSuite", "T.json")["testCases"]
    assert list(cases[0]["steps"][0]["formData"]) == ["FullName", "Save", "Save1", "Save2"]
    assert cases[0]["steps"][1]["formData"] == {"Name": "x"}  # the Other page's own "Name"
    assert cases[0]["steps"][2]["override"] == "Form|FullName|Zed"


@pytest.mark.parametrize("new, message", [("Save", "already has"), ("bad name", "letters"), ("a|b", "letters")])
def test_rename_field_rejects_bad_names(ws, new, message):
    _, pom, tests = ws
    with pytest.raises(RefactorError, match=message):
        Refactor(pom, tests).rename_field("SYS", "Form", "Name", new)


def test_merge_duplicates_uses_pipe_repeats_in_step_order(ws):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.merge_fields("SYS", "Form", "Save", ["Save1", "Save2"])
    apply_plan(r.plan())
    assert list(load(root, "Pages", "SYS", "Form.json")["fields"]) == ["Name", "Save", "Old", "Principal", "Principal1"]
    cases = load(root, "Tests", "SYS", "MasterRegressionSuite", "T.json")["testCases"]
    assert list(cases[0]["steps"][0]["formData"].items()) == [
        ("Name", "Ann"), ("Save", "click"), ("Save|", "click"), ("Save||", None)]
    assert cases[1]["steps"][0]["formData"] == {"Save|": "click"}  # untouched: it only used Save|


def test_merge_refuses_fields_that_differ_and_unmergeable_repeats(ws):
    root, pom, tests = ws
    with pytest.raises(RefactorError, match="exactly the same"):
        Refactor(pom, tests).merge_fields("SYS", "Form", "Principal", ["Principal1"])
    w(root / "Tests" / "SYS" / "MasterRegressionSuite" / "L.json", {"testCases": [{"testCaseID": "L", "system": "SYS",
        "steps": [{"screen": "Form", "formData": {"Save": "click", "Save1|ELoop(2)": "click", "Save2|ELoop(2)": "click"}}]}]})
    with pytest.raises(RefactorError, match="by hand"):
        Refactor(pom, tests).merge_fields("SYS", "Form", "Save", ["Save1", "Save2"])


def test_remove_refuses_used_fields(ws):
    _, pom, tests = ws
    with pytest.raises(RefactorError, match="still used"):
        Refactor(pom, tests).remove_fields("SYS", "Form", ["Name"])


def test_apply_is_all_or_nothing(ws, monkeypatch):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.rename_field("SYS", "Form", "Name", "FullName")
    plan = r.plan()
    before = {p: p.read_bytes() for p in (root / "Pages" / "SYS" / "Form.json",
                                           root / "Tests" / "SYS" / "MasterRegressionSuite" / "T.json",
                                           root / "Tests" / "com" / "c.json")}
    import webapp.services.refactor as mod
    real = mod.write_json
    calls = []

    def flaky(path, data, expected, indent=None):
        calls.append(path)
        if len(calls) == 3:
            raise OSError("disk full")
        return real(path, data, expected, indent)

    monkeypatch.setattr(mod, "write_json", flaky)
    with pytest.raises(OSError):
        apply_plan(plan)
    assert len(calls) == 3 and all(p.read_bytes() == b for p, b in before.items())


def test_apply_refuses_when_a_file_changed_since_the_preview(ws):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.rename_field("SYS", "Form", "Name", "FullName")
    plan = r.plan()
    (root / "Tests" / "com" / "c.json").write_text('{"commonActions": {}}', encoding="utf-8")
    with pytest.raises(ConflictError):
        apply_plan(plan)
    assert "Name" in load(root, "Pages", "SYS", "Form.json")["fields"]  # nothing was written


def test_tidy_finds_what_to_clean_and_the_plan_applies_it(ws):
    root, pom, tests = ws
    findings = {f.kind: f for f in analyze(pom, tests, "SYS", "Form")}
    assert findings["autogen"].names == ["Save", "Save1", "Save2"] and findings["autogen"].default
    assert findings["duplicate"].names == ["Save", "Save1", "Save2"] and not findings["duplicate"].default
    assert findings["unused"].names == ["Old", "Principal", "Principal1"]
    assert findings["twins"].names == ["Principal", "Principal1"] and not findings["twins"].fixable
    apply_plan(build_plan(pom, tests, "SYS", "Form", list(findings.values())))
    page = load(root, "Pages", "SYS", "Form.json")
    assert list(page["fields"]) == ["Name", "Save"] and "autoGen" not in page["fields"]["Save"]
    cases = load(root, "Tests", "SYS", "MasterRegressionSuite", "T.json")["testCases"]
    assert list(cases[0]["steps"][0]["formData"]) == ["Name", "Save", "Save|", "Save||"]
