"""Change impact: key-by-key diffs, who a change reaches (through common actions and reused tests), git."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from webapp.services.impact import (
    Change, ImpactError, analyze, app_changes, changes_from_git, diff_file, load_model,
)
from webapp.services.journeys import Journey

PAGE = {"frame": "", "fields": {"name": {"locator": "#name"}, "zip": {"locator": "#zip"},
                                "submit": {"locator": "button"}}}
TESTS = {"testCases": [
    {"testCaseID": "TC-1", "tags": ["smoke"], "steps": [{"screen": "Form", "formData": {"name": "a", "zip": "1"}}]},
    {"testCaseID": "TC-2", "tags": ["smoke"], "steps": [{"commonAction": "login"},
                                                        {"screen": "Other", "formData": {"x": "1"}}]},
    {"testCaseID": "TC-3", "tags": ["reg"], "steps": [{"testCase": "TC-1"}, {"screen": "Form",
                                                                            "formData": {"submit": ""}}]},
    {"testCaseID": "TC-4", "tags": ["reg"], "dataSet": "people", "steps": [{"screen": "Other",
                                                                           "formData": {"x": "1"}}]},
    {"testCaseID": "TC-5", "tags": [], "steps": [{"screen": "Form", "formData": {"zip|": "1"}}]},
]}
COMMON = {"commonActions": {"login": {"steps": [{"screen": "Login", "formData": {"user": "u", "pass": "p"}}]}}}


def write(root: Path, rel: str, data) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


@pytest.fixture
def ws(tmp_path):
    write(tmp_path, "Pages/S/Form.json", PAGE)
    write(tmp_path, "Pages/S/sub/Login.json", {"fields": {"user": {}, "pass": {}}})
    write(tmp_path, "Pages/S/Other.json", {"fields": {"x": {}}})
    write(tmp_path, "Tests/S/MasterRegressionSuite/a.json", TESTS)
    write(tmp_path, "Tests/com/commonActions.json", COMMON)
    write(tmp_path, "Data/systemData.json", {"S": {"dev": {"url": "http://x"}}, "T": {"dev": {}}})
    return tmp_path


def ids(report):
    return [t.test_id for t in report.tests]


def test_page_diff_is_field_by_field():
    new = json.loads(json.dumps(PAGE))
    new["fields"]["zip"]["locator"] = "#postal"
    new["fields"]["email"] = {"locator": "#email"}
    del new["fields"]["submit"]
    assert diff_file("Pages/S/Form.json", PAGE, new) == [
        Change("field", "Form", "S", "email", "added"), Change("field", "Form", "S", "submit", "removed"),
        Change("field", "Form", "S", "zip", "changed")]
    assert diff_file("Pages/S/Form.json", PAGE, {**PAGE, "frame": "#f"}) == [Change("page", "Form", "S")]
    assert diff_file("Pages/S/Form.json", None, PAGE) == [Change("page", "Form", "S", how="added")]
    for ignored in ("Data/journeys.json", "Data/locatorProfiles/S.json", "README.md", ".history/x.json"):
        assert diff_file(ignored, {"a": 1}, {"a": 2}) == []
    assert diff_file("Data/suiteConfig.json", {"a": 1}, {"a": 2}) == [Change("other", "Data/suiteConfig.json")]


def test_tests_common_data_and_systems_diffs():
    changed = json.loads(json.dumps(TESTS))
    changed["testCases"][0]["steps"].append({"screen": "Form"})
    changed["testCases"].pop(1)
    changed["testCases"].append({"testCaseID": "TC-9", "system": "T", "steps": []})
    assert diff_file("Tests/S/MasterRegressionSuite/a.json", TESTS, changed) == [
        Change("test", "TC-1", "S"), Change("test", "TC-2", "S", how="removed"),
        Change("test", "TC-9", "T", how="added")]
    assert diff_file("Tests/com/x.json", COMMON, {"commonActions": {}}) == [Change("common", "login", how="removed")]
    assert diff_file("Data/TestData/people.json", {"a": 1}, {"a": 2}) == [Change("data", "people")]
    assert diff_file("Data/systemData.json", {"S": {"dev": {}}, "T": {}}, {"S": {"dev": {"u": 1}}, "T": {}}) \
        == [Change("system", "S", "S")]


def test_a_field_change_reaches_direct_users_and_reusers(ws):
    report = analyze([Change("field", "Form", "S", "zip")], load_model(ws))
    assert ids(report) == ["TC-1", "TC-3", "TC-5"]  # TC-3 reuses TC-1; TC-5 fills zip again ("zip|")
    assert report.tests[1].reasons == ["uses Form > zip (changed)"]
    assert report.tests[2].runnable is False  # no tags: the framework cannot select it
    assert report.selection() == {"S": ["TC-1", "TC-3"]} and report.not_covered == []


def test_common_actions_data_files_and_breaking_changes(ws):
    model = load_model(ws)
    report = analyze([Change("common", "login", how="removed"), Change("data", "people"),
                      Change("field", "Form", "S", "submit", "removed")], model)
    assert ids(report) == ["TC-2", "TC-3", "TC-4"]
    assert [t.breaks for t in report.tests] == [True, True, False]  # breaking ones first
    assert report.tests[0].reasons == ["calls common action login, which was removed"]
    assert report.tests[2].reasons == ["reads data file people"]
    nested = analyze([Change("field", "Login", "S", "pass")], model)
    assert ids(nested) == ["TC-2"]  # a field used inside a common action


def test_changes_nobody_tests_and_journeys(ws):
    journeys = [Journey("reg", "Registration", "S", "critical", tests=["TC-1", "TC-9"]),
                Journey("other", "Other", "S", "normal", tests=["TC-4"]),
                Journey("t", "Elsewhere", "T", "high", tests=["TC-1"])]
    report = analyze([Change("field", "Form", "S", "email", "added"), Change("field", "Form", "S", "name"),
                      Change("page", "Nowhere", "S", how="app")], load_model(ws), journeys)
    assert [c.field or c.name for c in report.not_covered] == ["email", "Nowhere"]
    assert [(j.id, j.tests, j.of) for j in report.journeys] == [("reg", ["TC-1"], 2)]
    assert report.tests[0].journeys == ["Registration"]


def test_system_settings_and_unknown_files_reach_everything(ws):
    model = load_model(ws)
    assert ids(analyze([Change("system", "S", "S")], model)) == ["TC-1", "TC-2", "TC-3", "TC-4", "TC-5"]
    assert analyze([Change("system", "T", "T")], model).tests == []
    other = analyze([Change("other", "Data/suiteConfig.json")], model)
    assert len(other.tests) == 5 and other.everything == ["S"] and other.not_covered == []
    assert other.tests[0].reasons == ["a shared workspace file changed"]


def test_app_screens():
    assert app_changes({"S": ["Form", ""], "T": []}) == [Change("page", "Form", "S", how="app")]


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def test_changes_from_git_between_commits_and_in_the_working_tree(tmp_path):
    repo = tmp_path / "repo"
    ws = repo / "workspace"
    write(ws, "Pages/S/Form.json", PAGE)
    write(ws, "Tests/S/MasterRegressionSuite/a.json", TESTS)
    write(repo, "other/Pages/S/Form.json", PAGE)  # outside the workspace: ignored
    git(repo, "init", "-q")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "add", ".")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "base")
    changed = json.loads(json.dumps(PAGE))
    changed["fields"]["zip"]["locator"] = "#postal"
    write(ws, "Pages/S/Form.json", changed)
    write(repo, "other/Pages/S/Form.json", changed)
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "zip")
    assert changes_from_git(ws, "HEAD~1", "HEAD") == [Change("field", "Form", "S", "zip")]
    assert changes_from_git(ws, "HEAD") == []
    write(ws, "Pages/S/New.json", {"fields": {"a": {}}})  # not committed, not even added
    (ws / "Tests/S/MasterRegressionSuite/a.json").unlink()
    got = changes_from_git(ws, "HEAD")
    assert Change("page", "New", "S", how="added") in got
    assert Change("test", "TC-1", "S", how="removed") in got and len(got) == 6
    with pytest.raises(ImpactError):
        changes_from_git(ws, "no-such-branch")
    with pytest.raises(ImpactError):
        changes_from_git(ws, "--output=/tmp/x")
    with pytest.raises(ImpactError):
        changes_from_git(ws, "")
