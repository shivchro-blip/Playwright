"""A test case with a data set runs once per set, with each set's values typed in, on a local page."""
from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.test_runs import outcomes, run_and_wait


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make(workspace: Path, sets: dict) -> None:
    write(workspace / "Data" / "TestData" / "members.json", {"defaults": {"Taxed": "No"}, "sets": sets})
    write(workspace / "Pages" / "SMOKE" / "FormsPage.json", {"path": "/forms.html", "fields": {
        "member": {"locator": "input.member", "index": 0, "type": "text"},
        "Taxed": {"locator": "[data-attribute-id='Taxed'] button:text-is('{value}')", "type": "button"}}})
    write(workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Data.json", {"testCases": [{
        "testCaseID": "TC-DATA", "system": "SMOKE", "env": "dev", "module": "m", "tags": ["dataset"],
        "dataSet": "members",
        "steps": [{"screen": "FormsPage", "formData": {"member": "DATA.Name", "Taxed": "DATA.Taxed"}}]}]})


def test_one_run_per_set_with_each_sets_values(workspace: Path, tmp_path: Path):
    make(workspace, {"ann": {"Name": "Ann"}, "bob": {"Name": "Bob", "Taxed": "Yes"}})
    run = run_and_wait(workspace, tmp_path, tags=["dataset"])
    log = "\n".join(run.log)
    assert outcomes(run) == {"TC-DATA[ann]": "passed", "TC-DATA[bob]": "passed"}, log[-3000:]
    assert "Filled 'member' with 'Ann'" in log and "Filled 'member' with 'Bob'" in log


def test_run_data_set_picks_one_set(workspace: Path, tmp_path: Path):
    make(workspace, {"ann": {"Name": "Ann"}, "bob": {"Name": "Bob"}})
    run = run_and_wait(workspace, tmp_path, tags=["dataset"],
                       options={"headlessMode": True, "runDataSet": "bob"})
    assert outcomes(run) == {"TC-DATA[bob]": "passed"}, "\n".join(run.log[-40:])


def test_a_missing_value_stops_the_run_with_the_reason(workspace: Path, tmp_path: Path):
    make(workspace, {"ann": {"Name": "Ann"}, "bob": {}})  # bob has no Name
    run = run_and_wait(workspace, tmp_path, tags=["dataset"])
    log = "\n".join(run.log)
    assert run.status == "error" and "TC-DATA[bob]" in log and "no value 'Name'" in log, log[-2000:]
