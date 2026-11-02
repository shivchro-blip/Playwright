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


def test_cli_data_set_option_runs_one_set(workspace: Path, tmp_path: Path):
    from tests.e2e.test_cli_run import cli

    make(workspace, {"ann": {"Name": "Ann"}, "bob": {"Name": "Bob"}})
    done = cli(workspace, tmp_path, "--tags", "dataset", "--data-set", "bob")
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]
    assert "TC-DATA[bob]" in done.stdout and "TC-DATA[ann]" not in done.stdout


def test_from_step_dialog_picks_one_set_of_the_data_file(workspace: Path, tmp_path: Path):
    from playwright.sync_api import expect, sync_playwright

    from tests.e2e.test_ui import running_app

    make(workspace, {"ann": {"Name": "Ann"}, "bob": {"Name": "Bob"}})
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text("TC-DATA").first.click()
        page.get_by_test_id("run-from-step").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_test_id("rs-set").click()
        page.get_by_role("option", name="bob").click()
        dialog.get_by_test_id("rs-run").click()
        expect(page.locator(".any-headline")).to_have_text("Passed", timeout=180_000)
        browser.close()
    run_json = sorted((tmp_path / "runs").glob("*/run.json"))[-1]
    data = json.loads(run_json.read_text(encoding="utf-8"))
    assert data["config"]["options"]["runDataSet"] == "bob"
    assert [r["test_id"] for r in data["results"]] == ["TC-DATA[bob]"]
