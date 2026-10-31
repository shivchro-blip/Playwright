"""Run a test case from a step: checkpoints saved by one run, resumed by the next, on a local three-step page.

The page keeps its progress in localStorage and a token in sessionStorage; step 3's Finish button stays disabled
without the token, so a run from step 3 only passes if the saved browser state really was restored.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import outcomes, run_and_wait
from tests.e2e.test_ui import running_app
from webapp.services.checkpoints import CheckpointStore

TEST_ID = "TC-RESUME"


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_flow(workspace: Path) -> None:
    pages = workspace / "Pages" / "SMOKE"
    write(pages / "Wiz1.json", {"path": "/steps.html", "fields": {"next1": {"locator": "#next1", "type": "button"}}})
    write(pages / "Wiz2.json", {"fields": {"city": {"locator": "#city", "type": "text"},
                                           "next2": {"locator": "#next2", "type": "button"}}})
    write(pages / "Wiz3.json", {"fields": {
        "finish": {"locator": "#finish", "type": "button"},
        "done": {"locator": "#done", "type": "text", "contains": "Done"}}})
    write(workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Resume.json", {"testCases": [{
        "testCaseID": TEST_ID, "system": "SMOKE", "env": "dev", "module": "m", "tags": ["resume"], "steps": [
            {"screen": "Wiz1", "formData": {"next1": "click"}},
            {"screen": "Wiz2", "formData": {"city": "Albany", "next2": "click"}},
            {"screen": "Wiz3", "formData": {"finish": "click", "done": {"type": "confirmtext"}}}]}]})


def screens_run(run) -> list[str]:
    names = []
    for f in run.results_dir.glob("*-result.json"):
        names += [s["name"] for s in json.loads(f.read_text(encoding="utf-8")).get("steps", [])]
    return names


def test_a_run_saves_checkpoints_and_the_next_run_resumes_from_one(workspace: Path, tmp_path: Path):
    make_flow(workspace)
    full = run_and_wait(workspace, tmp_path, tags=["resume"], test_ids=[TEST_ID],
                        options={"headlessMode": True, "saveCheckpoints": True})
    assert outcomes(full) == {TEST_ID: "passed"}, "\n".join(full.log[-40:])
    assert screens_run(full) == ["- Screen: Wiz1 -", "- Screen: Wiz2 -", "- Screen: Wiz3 -"]

    saved = CheckpointStore(tmp_path / "runs").for_test(TEST_ID)
    assert sorted(saved) == [1, 2, 3] and saved[2].run_id == full.id and saved[2].url.endswith("/steps.html")
    state = json.loads(saved[2].path.read_text(encoding="utf-8"))
    origin = next(o for o in state["storage_state"]["origins"] if o["origin"].startswith("http"))
    assert {"name": "step", "value": "3"} in origin["localStorage"]  # progress after step 2
    assert state["session_storage"]["items"] == {"token": "t-123"}  # and the sessionStorage token

    resumed = run_and_wait(workspace, tmp_path, tags=["resume"], test_ids=[TEST_ID],
                           options={"headlessMode": True, "runFromStep": 3, "resumeCheckpoint": str(saved[2].path)})
    assert outcomes(resumed) == {TEST_ID: "passed"}, "\n".join(resumed.log[-40:])
    assert screens_run(resumed) == ["- Screen: Wiz3 -"]  # steps 1 and 2 were not run again
    assert any("Running steps 3-3 of 3" in line for line in resumed.log)


def test_to_step_stops_early_and_start_url_opens_a_page_after_signing_in(workspace: Path, tmp_path: Path,
                                                                         site_url: str):
    make_flow(workspace)
    early = run_and_wait(workspace, tmp_path, tags=["resume"], test_ids=[TEST_ID],
                         options={"headlessMode": True, "runToStep": 2})
    assert outcomes(early) == {TEST_ID: "passed"}, "\n".join(early.log[-40:])
    assert screens_run(early) == ["- Screen: Wiz1 -", "- Screen: Wiz2 -"]

    # step 2 starts on the page step 1 left: a start URL takes the place of step 1's navigation
    url = run_and_wait(workspace, tmp_path, tags=["resume"], test_ids=[TEST_ID],
                       options={"headlessMode": True, "runFromStep": 1, "runToStep": 1,
                                "startUrl": f"{site_url}/steps.html"})
    assert outcomes(url) == {TEST_ID: "passed"} and screens_run(url) == ["- Screen: Wiz1 -"]


def test_without_the_saved_state_the_same_run_fails(workspace: Path, tmp_path: Path, site_url: str):
    """The control for the resume test: opening the page without the restored state leaves step 3's button
    disabled, so the run only passes above because the browser state really came back."""
    make_flow(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["resume"], test_ids=[TEST_ID],
                       options={"headlessMode": True, "runFromStep": 3, "startUrl": f"{site_url}/steps.html"})
    assert outcomes(run) == {TEST_ID: "failed"}


def test_from_step_dialog_offers_the_saved_state_and_starts_the_run(workspace: Path, tmp_path: Path):
    make_flow(workspace)
    full = run_and_wait(workspace, tmp_path, tags=["resume"], test_ids=[TEST_ID],
                        options={"headlessMode": True, "saveCheckpoints": True})
    assert full.status == "passed", "\n".join(full.log[-40:])
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_text(TEST_ID).first.click()
        page.get_by_test_id("run-from-step").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_test_id("rs-mode")).to_contain_text("From the start")
        dialog.get_by_test_id("rs-first").click()
        page.get_by_role("option", name="3. Page Wiz3").click()
        expect(dialog.get_by_test_id("rs-mode")).to_contain_text("Saved state after step 2")
        expect(dialog.get_by_test_id("rs-mode")).to_contain_text(full.id)
        dialog.get_by_test_id("rs-run").click()
        expect(page.locator(".any-headline")).to_have_text("Passed", timeout=180_000)
        browser.close()
    runs = sorted((tmp_path / "runs").glob("*/run.json"))
    options = json.loads(runs[-1].read_text(encoding="utf-8"))["config"]["options"]
    assert options["runFromStep"] == 3 and options["resumeCheckpoint"].endswith("/TC-RESUME/2.json")
    assert "saveCheckpoints" not in options
