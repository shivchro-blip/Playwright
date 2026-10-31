"""Opt-in runner features, run for real against a local page: {value} locators, step-level index, date picker."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from tests.e2e.test_runs import outcomes, run_and_wait


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_value_placeholder_step_index_and_date_picker(workspace: Path, tmp_path: Path):
    expected = f"{date.today() + timedelta(days=30):%m/%d/%Y}"
    write(workspace / "Pages" / "SMOKE" / "FormsPage.json", {"path": "/forms.html", "fields": {
        "Taxed": {"locator": "[data-attribute-id='Taxed'] button:text-is('{value}')", "type": "button"},
        "Home": {"locator": "[data-attribute-id='Home'] button:has-text('{value}')", "type": "button"},
        "answers": {"locator": "#answers", "type": "text", "contains": "taxed=No home=Yes"},
        "BeginDate": {"locator": "#begin", "type": "datepicker"},
        "beginEcho": {"locator": "#begin-echo", "type": "text", "contains": expected},
        "member": {"locator": "input.member", "index": 0, "type": "text", "indexed": True},
        "echo0": {"locator": ".echo", "index": 0, "type": "text", "contains": "Ann"},
        "echo1": {"locator": ".echo", "index": 1, "type": "text", "contains": "Bob"},
    }})
    case = {"system": "SMOKE", "env": "dev", "module": "authoring", "tags": ["authoring"]}
    write(workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Authoring.json", {"testCases": [
        {**case, "testCaseID": "TC-AUTH-OK", "steps": [
            {"screen": "FormsPage", "formData": {
                "Taxed": "No", "Home": "Yes", "answers": {"type": "confirmtext"},
                "BeginDate": "+30d", "beginEcho": {"type": "confirmtext"},
                "member": "Ann", "echo0": {"type": "confirmtext"}}},
            {"screen": "FormsPage", "index": 1, "formData": {"member": "Bob", "echo1": {"type": "confirmtext"}}},
        ]},
        {**case, "testCaseID": "TC-AUTH-NOVALUE", "steps": [
            {"screen": "FormsPage", "formData": {"Taxed": None}},
        ]},
    ]})

    run = run_and_wait(workspace, tmp_path, tags=["authoring"])
    log = "\n".join(run.log)
    assert outcomes(run) == {"TC-AUTH-OK": "passed", "TC-AUTH-NOVALUE": "failed"}, log[-3000:]
    assert "needs a value" in log and "\"Taxed\": \"No\"" in log
