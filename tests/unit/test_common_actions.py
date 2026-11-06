"""Common actions: listing, checking, saving, renaming everywhere, and refusing to delete one that is called."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from webapp.services.common_actions import CommonActionService
from webapp.services.json_files import ConflictError, PathError, read_json
from webapp.services.pom_service import PomService
from webapp.services.test_case_service import TestCaseService

LOGIN = {"steps": [{"screen": "Login", "formData": {"user": "ENV.USER", "password|": "ENV.PW", "go": "click"}}]}


@pytest.fixture
def ws(tmp_path):
    def write(rel, data):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    write("Pages/S/Login.json", {"path": "/login", "fields": {"user": {"locator": "#u"}, "password": {"locator": "#p"},
                                                                "go": {"locator": "#go"}}})
    write("Tests/com/commonActions.json", {"commonActions": {"login": LOGIN, "logout": {"steps": []}}})
    write("Tests/com/extra.json", {"commonActions": {"other": {"steps": []}}})
    write("Tests/S/MasterRegressionSuite/A.json", {"testCases": [
        {"testCaseID": "TC-1", "system": "S", "steps": [{"commonAction": "login"}, {"commonAction": "login",
                                                                                    "override": "Login|user|bob"}]},
        {"testCaseID": "TC-2", "system": "S", "steps": [{"screen": "Login", "formData": {"user": "x"}}]}]})
    write("Tests/S/MasterRegressionSuite/B.json", {"testCases": [
        {"testCaseID": "TC-3", "system": "S", "steps": [{"commonAction": "login"}]}]})
    return tmp_path


@pytest.fixture
def svc(ws):
    return CommonActionService(ws, TestCaseService(ws, PomService(ws, ws)))


def test_list_shows_file_steps_and_callers(svc):
    rows = {a.name: a for a in svc.list()}
    assert list(rows) == ["login", "logout", "other"]
    assert (rows["login"].file, rows["login"].steps, rows["login"].used_by) == ("commonActions.json", 1, 3)
    assert rows["other"].file == "extra.json" and rows["logout"].used_by == 0
    assert svc.used_by("login") == ["TC-1 step 1", "TC-1 step 2", "TC-3 step 1"]


def test_parameters_are_the_fields_a_call_can_override(svc):
    action, file, _ = svc.load("login")
    assert file == "commonActions.json"
    assert [(p.screen, p.field, p.value, p.override) for p in svc.parameters(action)] == [
        ("Login", "user", "ENV.USER", "Login|user|<value>"), ("Login", "password", "ENV.PW", "Login|password|<value>"),
        ("Login", "go", "click", "Login|go|<value>")]


def test_check_errors_block_and_warnings_only_inform(svc):
    assert svc.check(LOGIN).errors == [] and svc.check(LOGIN).warnings == []
    assert "steps" in svc.check([]).errors[0]
    bad = {"steps": [5, {"formData": {}}, {"screen": "Login", "formData": []}]}
    assert len(svc.check(bad).errors) == 3
    odd = svc.check({"steps": [{"screen": "Nowhere", "formData": {}},
                               {"screen": "Login", "formData": {"user": "a", "zip|": "1"}}]})
    assert odd.errors == [] and "no page named 'Nowhere'" in odd.warnings[0] and "no field zip" in odd.warnings[1]


def test_save_writes_into_the_actions_own_file_and_keeps_the_rest(svc, ws):
    action, _, sha = svc.load("other")
    new_sha = svc.save("other", {"steps": [{"screen": "Login", "formData": {"go": "click"}}]}, sha)
    data = json.loads((ws / "Tests/com/extra.json").read_text(encoding="utf-8"))
    assert data["commonActions"]["other"]["steps"][0]["formData"] == {"go": "click"} and new_sha
    assert "login" in json.loads((ws / "Tests/com/commonActions.json").read_text())["commonActions"]
    with pytest.raises(ConflictError):
        svc.save("other", {"steps": []}, sha)  # stale hash
    with pytest.raises(ValueError, match="screen"):
        svc.save("other", {"steps": [{"formData": {}}]}, new_sha)


def test_create_and_delete(svc, ws):
    assert svc.create(" fill address ", "commonActions.json") == "fill address"
    assert svc.load("fill address")[0] == {"steps": []}
    with pytest.raises(PathError, match="already exists"):
        svc.create("login")
    for bad in ("", "a/b", "x" * 81):
        with pytest.raises(PathError):
            svc.create(bad)
    svc.create("brand new", "sub/new.json")
    assert json.loads((ws / "Tests/com/sub/new.json").read_text())["commonActions"] == {"brand new": {"steps": []}}
    svc.delete("fill address")
    assert svc.locate("fill address") is None
    with pytest.raises(ConflictError, match="called by TC-1, TC-3"):
        svc.delete("login")
    assert svc.locate("login") is not None


def test_rename_updates_every_call_and_the_history_keeps_the_old_files(svc, ws):
    assert svc.rename_preview("login", "sign in") == ["S/A.json", "S/B.json"]
    assert svc.rename("login", "sign in") == 3
    a = json.loads((ws / "Tests/S/MasterRegressionSuite/A.json").read_text())
    assert [s.get("commonAction") for s in a["testCases"][0]["steps"]] == ["sign in", "sign in"]
    assert a["testCases"][0]["steps"][1]["override"] == "Login|user|bob"
    assert list(json.loads((ws / "Tests/com/commonActions.json").read_text())["commonActions"]) == ["sign in", "logout"]
    assert svc.used_by("sign in") and not svc.used_by("login")
    from webapp.services import history
    assert history.versions(ws / "Tests/S/MasterRegressionSuite/A.json")  # undo is possible
    with pytest.raises(PathError, match="already exists"):
        svc.rename("sign in", "logout")
    with pytest.raises(PathError, match="No common action"):
        svc.rename("login", "x")
    with pytest.raises(PathError, match="Name"):
        svc.rename("sign in", "bad/name")
    assert read_json(Path(ws / "Tests/com/commonActions.json"))[0]["commonActions"]["sign in"] == LOGIN
