"""A recorded test must run: fake codegen -> save_recording -> a real run of the saved test case."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from tests.e2e.test_runs import outcomes, run_and_wait
from webapp.services.pom_service import PomService
from webapp.services.recorder import RecordOptions, Recorder, save_recording, save_wizard
from webapp.services.test_case_service import TestCaseService as CaseService  # pytest collects Test* names

FAKE_CODEGEN = Path(__file__).resolve().parent / "fixtures" / "recorder" / "fake_codegen.py"


def test_recorded_test_runs_and_passes(workspace: Path, site_url: str, tmp_path: Path):
    recorder = Recorder(command=[sys.executable, str(FAKE_CODEGEN)])
    recorder.start(f"{site_url}/index.html")
    deadline = time.monotonic() + 30
    while recorder.active and time.monotonic() < deadline:
        time.sleep(0.1)
    session = recorder.session
    assert session and session.status == "finished", session and session.error

    pom = PomService(workspace, workspace)
    saved = save_recording(pom, CaseService(workspace, pom), "SMOKE", session.recording, page_name="SmokeHome",
                           base_url=site_url, test_id="TC-SMOKE-REC-001", test_file="Recorded.json", env="dev",
                           tags=["E2E", "recorded"])
    assert saved.renames == {"q": "searchBox", "notes": "notes2", "phone": "phone"}  # merged into SmokeHome

    run = run_and_wait(workspace, tmp_path, tags=["recorded"])
    assert run.status == "passed", "\n".join(run.log[-80:])
    assert outcomes(run) == {"TC-SMOKE-REC-001": "passed"}


def test_recorded_wizard_becomes_a_page_per_screen_and_runs(workspace: Path, site_url: str, tmp_path: Path):
    """Later screens are hidden until the earlier one's Save & Continue is clicked, so the run only passes when
    the split pages and their steps reproduce the whole flow in order."""
    fake = Path(__file__).resolve().parent / "fixtures" / "recorder" / "fake_codegen_wizard.py"
    recorder = Recorder(command=[sys.executable, str(fake)])
    recorder.start(f"{site_url}/wizard.html", options=RecordOptions(
        break_on=("Save & Continue",), id_attribute="data-attribute-id", id_prefix="LSI_DEMO_"))
    deadline = time.monotonic() + 30
    while recorder.active and time.monotonic() < deadline:
        time.sleep(0.1)
    session = recorder.session
    assert session and session.status == "finished", session and session.error
    assert [s.name for s in session.recording.screens] == ["Screen1", "Screen2", "Screen3"]

    pom = PomService(workspace, workspace)
    names = ["WizardBusiness", "WizardTax", "WizardSubmit"]
    save_wizard(pom, CaseService(workspace, pom), "SMOKE", session.recording, page_names=names, base_url=site_url,
                test_id="TC-SMOKE-WIZARD", test_file="Wizard.json", env="dev", tags=["E2E", "wizard"])
    business, _ = pom.load("SMOKE", "WizardBusiness.json")
    assert business["path"] == "/wizard.html"
    assert business["fields"]["BusName"]["locator"] == "[data-attribute-id='LSI_DEMO_BusName'] input"
    home = business["fields"]["HomeBased"]["locator"]
    assert home == "[data-attribute-id='LSI_DEMO_HomeBased'] button:has-text('No')"

    run = run_and_wait(workspace, tmp_path, tags=["wizard"])
    assert run.status == "passed", "\n".join(run.log[-80:])
    assert outcomes(run) == {"TC-SMOKE-WIZARD": "passed"}
    steps = [line for line in run.log if "Screen:" in line]
    assert any("WizardBusiness" in s for s in steps) and any("WizardSubmit" in s for s in steps), run.log[-40:]
