"""The workspace model answers exactly as the code it replaces (usage, impact, journey coverage), and only re-reads
files that changed."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from webapp.services import impact
from webapp.services.journeys import JourneyService, JourneyStore
from webapp.services.pom_service import PomService
from webapp.services.test_case_service import TestCaseService
from webapp.services.usage import UsageIndex
from webapp.services.workspace_model import WorkspaceModel, model_for

REPO = Path(__file__).resolve().parents[2]


def write(root: Path, rel: str, data) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def rich_workspace(root: Path) -> Path:
    """Common actions calling common actions, reused tests, overrides, repeated keys, a case with its own system,
    data sets, journeys and a page in a sub-folder."""
    write(root, "Pages/S/Form.json", {"fields": {"name": {}, "zip": {}, "submit": {}}})
    write(root, "Pages/S/sub/Login.json", {"fields": {"user": {}, "pass": {}}})
    write(root, "Pages/S/Other.json", {"fields": {"x": {}}})
    write(root, "Pages/T/Elsewhere.json", {"fields": {"y": {}}})
    write(root, "Tests/S/MasterRegressionSuite/a.json", {"testCases": [
        {"testCaseID": "TC-1", "tags": ["smoke"],
         "steps": [{"screen": "Form", "formData": {"name": "a", "zip|": "1"}}]},
        {"testCaseID": "TC-2", "tags": ["smoke"], "dataSet": "people",
         "steps": [{"commonAction": "login", "override": "Login|user|ENV.U"},
                   {"screen": "Other", "formData": {"x": 1}}]},
        {"testCaseID": "TC-3", "tags": ["reg"],
         "steps": [{"testCase": "TC-1"}, {"screen": "Form", "formData": {"submit": ""}}]},
        {"testCaseID": "TC-T", "system": "T", "tags": ["t"],
         "steps": [{"screen": "Elsewhere", "formData": {"y": 1}}]}]})
    write(root, "Tests/S/MasterRegressionSuite/deep/b.json", {"testCases": [
        {"testCaseID": "TC-4", "tags": ["reg"], "steps": [{"commonAction": "wrapper"}]}]})
    write(root, "Tests/com/commonActions.json", {"commonActions": {
        "login": {"steps": [{"screen": "Login", "formData": {"user": "u", "pass": "p"}}]},
        "wrapper": {"steps": [{"commonAction": "login"}, {"screen": "Form", "formData": {"name": "w"}}]}}})
    write(root, "Data/TestData/people.json", {"sets": {"a": {"x": 1}}})
    write(root, "Data/journeys.json", {"journeys": [{"id": "reg", "name": "Registration", "system": "S",
                                                     "tests": ["TC-1", "TC-3"]}]})
    write(root, "Data/systemData.json", {"S": {}, "T": {}})
    return root


@pytest.fixture(params=["rich", "e2e-fixture", "repo-workspace"])
def ws(request, tmp_path) -> Path:
    if request.param == "rich":
        return rich_workspace(tmp_path / "rich")
    source = REPO / ("tests/e2e/fixtures/workspace" if request.param == "e2e-fixture" else "workspace")
    target = tmp_path / request.param
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("reports", ".history", ".env"))
    return target


def services(ws: Path):
    pom = PomService(ws, ws)
    return pom, TestCaseService(ws, pom)


def test_used_by_is_the_same_as_the_usage_scan(ws):
    _, tests = services(ws)
    snap = WorkspaceModel(ws, tests).snapshot()
    for system in snap.systems() + ["NOPE"]:
        old = UsageIndex(tests).scan_files(system)  # the file-reading reference
        new = snap.usage(system)
        assert dict(new.pages) == dict(old.pages), system
        assert dict(new.fields) == dict(old.fields), system
        via = UsageIndex(tests).scan(system)  # what Used by, rename and tidy now get
        assert dict(via.pages) == dict(old.pages) and dict(via.fields) == dict(old.fields)


def test_footprints_and_impact_are_the_same(ws):
    _, tests = services(ws)
    snap = WorkspaceModel(ws, tests).snapshot()
    old = impact.load_model_files(ws)
    assert set(snap.tests) == set(old.tests)
    for (system, tid), node in old.tests.items():
        fp_old, fp_new = impact.footprint(node, old), snap.footprint(system, tid)
        assert (fp_new.pages, fp_new.fields, fp_new.common, fp_new.tests) == \
            (fp_old.pages, fp_old.fields, fp_old.common, fp_old.tests), tid
    assert snap.common == old.common
    assert impact.load_model(ws) == old  # impact now builds its model from the workspace model


def test_journey_indexes_are_the_same(ws):
    pom, tests = services(ws)
    snap = WorkspaceModel(ws, tests).snapshot()
    service = JourneyService(JourneyStore(ws), tests, SimpleNamespace(), None, None, None, lambda run: {})
    for system in snap.systems():
        assert service._steps_index_files(system) == snap.by_folder.get(system, {}), system
        assert service._fields_index_files(system) == {p: f for (s, p), f in snap.pages.items() if s == system}, system
        assert service._steps_index(system) == service._steps_index_files(system)  # the service now uses the model


def test_reach_search_and_lookups_on_the_rich_workspace(tmp_path):
    ws = rich_workspace(tmp_path)
    snap = WorkspaceModel(ws).snapshot()
    assert snap.tests_reaching("S", page="Login") == ["TC-2", "TC-4"]  # TC-4 through wrapper -> login
    assert snap.tests_reaching("S", page="Form", field_name="zip") == ["TC-1", "TC-3"]  # TC-3 reuses TC-1
    assert snap.tests_reaching("S", common="login") == ["TC-2", "TC-4"]
    assert snap.tests_reaching("S", test="TC-1") == ["TC-1", "TC-3"]
    assert snap.tests_reaching("S", data_set="people") == ["TC-2"]
    assert snap.tests_reaching("T", page="Elsewhere") == ["TC-T"]  # a case with its own system
    assert snap.fields_of("S", "Login") == {"user", "pass"} and snap.page_files[("S", "Login")] == "sub/Login.json"
    assert snap.data_sets == ["people"] and snap.journeys[0]["id"] == "reg"
    assert [h["kind"] for h in snap.search("log")] == ["page", "common"]
    assert snap.search("zip") == [{"kind": "field", "name": "zip", "system": "S", "page": "Form"}]
    assert snap.search("") == []


def test_only_changed_files_are_read_again(tmp_path):
    ws = rich_workspace(tmp_path)
    model = WorkspaceModel(ws)
    first = model.snapshot()
    reads = model.files.reads
    assert model.snapshot() is first and model.files.reads == reads  # nothing changed: same snapshot, no reads
    page = ws / "Pages/S/Other.json"
    time.sleep(0.01)
    write(ws, "Pages/S/Other.json", {"fields": {"x": {}, "x2": {}}})
    os.utime(page, ns=(time.time_ns(), time.time_ns()))
    second = model.snapshot()
    assert second is not first and second.fields_of("S", "Other") == {"x", "x2"}
    assert model.files.reads == reads + 1  # just that one file
    (ws / "Data/TestData/people.json").unlink()
    assert model.snapshot().data_sets == []
    assert model_for(ws) is model_for(ws) and model_for(ws) is not model


def test_an_app_save_is_seen_even_when_size_and_time_look_unchanged(tmp_path):
    from webapp.services.json_files import read_json, write_json
    ws = rich_workspace(tmp_path)
    (ws / "Pages").mkdir(exist_ok=True)
    model = WorkspaceModel(ws)
    page = ws / "Pages/S/Other.json"
    _, sha, _ = read_json(page)
    sha = write_json(page, {"fields": {"x": {}}}, sha, indent=" ")
    assert model.snapshot().fields_of("S", "Other") == {"x"}
    before = page.stat()
    write_json(page, {"fields": {"z": {}}}, sha, indent=" ")  # the same length
    os.utime(page, ns=(before.st_atime_ns, before.st_mtime_ns))  # as a coarse clock would leave it
    assert page.stat().st_size == before.st_size and page.stat().st_mtime_ns == before.st_mtime_ns
    assert model.snapshot().fields_of("S", "Other") == {"z"}
