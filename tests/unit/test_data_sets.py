"""Test data sets: DATA.<key> values, one test case over many sets, the files and their validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.data_sets import DataSetError, expand, load_file, references, substitute, validate
from webapp.services.data_sets import DataSetService
from webapp.services.json_files import ConflictError
from webapp.services.pom_service import PomService
from webapp.services.test_case_service import TestCaseService as CaseService

FILE = {"description": "applicants", "defaults": {"Country": "United States"},
        "sets": {"albany": {"City": "Albany", "Zip": "12206"}, "nyc": {"City": "New York", "Zip": 10001}}}
CASE = {"testCaseID": "TC-1", "dataSet": "applicants", "steps": [
    {"screen": "Address", "formData": {"City": "DATA.City", "Zip": "DATA.Zip", "Country": "DATA.Country",
                                        "Note": "Lives in DATA.City", "Plain": "x", "Count": 3,
                                        "Full": "DATA.City|DATA.Zip", "Obj": {"type": "fill", "value": "DATA.City"},
                                        "Skip": None}},
    {"commonAction": "Fill", "override": "Address|City|DATA.City"}]}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "Data" / "TestData").mkdir(parents=True)
    (tmp_path / "Data" / "TestData" / "applicants.json").write_text(json.dumps(FILE), encoding="utf-8")
    return tmp_path


def test_values_are_replaced_only_when_they_are_exactly_a_reference(workspace):
    cases = expand([CASE], workspace)
    assert [c["testCaseID"] for c in cases] == ["TC-1[albany]", "TC-1[nyc]"]
    form = cases[0]["steps"][0]["formData"]
    assert form["City"] == "Albany" and form["Zip"] == "12206" and form["Country"] == "United States"
    assert form["Note"] == "Lives in DATA.City"  # embedded text is left alone
    assert form["Plain"] == "x" and form["Count"] == 3 and form["Skip"] is None
    assert form["Full"] == "Albany|12206" and form["Obj"] == {"type": "fill", "value": "Albany"}
    assert cases[0]["steps"][1]["override"] == "Address|City|Albany"
    assert cases[1]["steps"][0]["formData"]["Zip"] == "10001"  # numbers become text
    assert cases[1]["baseTestCaseID"] == "TC-1" and cases[1]["dataSetName"] == "nyc"
    assert CASE["steps"][0]["formData"]["City"] == "DATA.City"  # the loaded test case is not changed


def test_cases_without_a_data_set_pass_through_and_one_set_can_be_chosen(workspace):
    plain = {"testCaseID": "TC-2", "steps": []}
    assert expand([plain], workspace) == [plain]
    only = expand([CASE, plain], workspace, only_set="nyc")
    assert [c["testCaseID"] for c in only] == ["TC-1[nyc]", "TC-2"]
    with pytest.raises(DataSetError, match="no set 'paris'"):
        expand([CASE], workspace, only_set="paris")


def test_a_missing_value_names_the_set_and_what_it_has(workspace):
    bad = {"testCaseID": "TC-3", "dataSet": "applicants",
           "steps": [{"screen": "A", "formData": {"State": "DATA.State"}}]}
    with pytest.raises(DataSetError, match=r"TC-3\[albany\].*no value 'State'.*City, Country, Zip"):
        expand([bad], workspace)


@pytest.mark.parametrize("data, message", [
    ([], "sets"), ({"sets": {}}, "sets"), ({"sets": {"a": []}}, "must be an object"),
    ({"sets": {"a": {"1bad": "x"}}}, "Key '1bad'"), ({"sets": {"a b": {}}}, "Set name"),
    ({"sets": {"a": {"k": ["x"]}}}, "text or a number"), ({"defaults": [], "sets": {"a": {}}}, "defaults"),
])
def test_files_are_validated(data, message):
    with pytest.raises(DataSetError, match=message):
        validate(data, "f")


def test_load_reports_missing_unreadable_and_badly_named_files(workspace):
    with pytest.raises(DataSetError, match="not found"):
        load_file(workspace, "nope")
    (workspace / "Data" / "TestData" / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(DataSetError, match="cannot be read"):
        load_file(workspace, "broken")
    with pytest.raises(DataSetError, match="letters"):
        load_file(workspace, "../x")


def test_references_and_substitute_helpers():
    assert references(CASE["steps"]) == {"City", "Zip", "Country"}
    assert references("a|DATA.x| DATA.y ") == {"x", "y"} and references(5) == set()
    assert substitute(["DATA.a", {"k": "DATA.a"}, 1], {"a": "v"}, "t") == ["v", {"k": "v"}, 1]


@pytest.fixture
def service(workspace: Path) -> DataSetService:
    pom = PomService(workspace, workspace)
    tests = CaseService(workspace, pom)
    suite = workspace / "Tests" / "SYS" / "MasterRegressionSuite"
    suite.mkdir(parents=True)
    (suite / "T.json").write_text(json.dumps({"testCases": [CASE]}), encoding="utf-8")
    return DataSetService(workspace, tests)


def test_service_lists_reads_keys_and_who_uses_a_file(service):
    [ref] = service.list()
    assert (ref.name, ref.sets, ref.keys, ref.used_by) == ("applicants", 2, 3, 1)
    assert service.keys("applicants") == ["Country", "City", "Zip"] and service.used_by("applicants") == ["TC-1"]


def test_service_create_save_conflict_and_delete_rules(service):
    name = service.create("second")
    data, sha = service.load(name)
    assert data["sets"] == {"default": {}}
    data["sets"]["x"] = {"A": "1"}
    new = service.save(name, data, sha)
    with pytest.raises(ConflictError):
        service.save(name, data, sha)  # stale hash
    with pytest.raises(DataSetError):
        service.save(name, {"sets": {}}, new)
    with pytest.raises(Exception, match="already exists"):
        service.create("second")
    with pytest.raises(ConflictError, match="used by TC-1"):
        service.delete("applicants", service.load("applicants")[1])
    service.delete("second", new)
    assert service.names() == ["applicants"]


def test_check_warns_about_missing_files_unset_values_and_unused_data_sets(workspace):
    pom = PomService(workspace, workspace)
    tests = CaseService(workspace, pom)
    ok = tests.validate("SYS", {**CASE, "steps": [{"commonAction": "x", "override": "A|b|DATA.City"}]})
    assert not any("DATA" in w or "data set" in w for w in ok.warnings)
    short = tests.validate("SYS", {**CASE, "steps": [{"commonAction": "x", "override": "A|b|DATA.State"}]})
    assert any("DATA.State is not defined in data set 'applicants' for: albany, nyc" in w for w in short.warnings)
    gone = tests.validate("SYS", {**CASE, "dataSet": "nope"})
    assert any("'nope' not found" in w for w in gone.warnings)
    none = tests.validate("SYS", {k: v for k, v in CASE.items() if k != "dataSet"})
    assert any("but the test case has no data set" in w for w in none.warnings)
