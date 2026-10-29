"""A recorded test must run: fake codegen -> save_recording -> a real run of the saved test case."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from tests.e2e.test_runs import outcomes, run_and_wait
from webapp.services.pom_service import PomService
from webapp.services.recorder import Recorder, save_recording
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
