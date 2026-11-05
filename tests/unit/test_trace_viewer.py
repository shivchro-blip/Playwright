"""Opening a run's trace in Playwright's viewer on the app's own machine."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from webapp.services.trace_viewer import blocker, open_trace

FAKE = "import sys, pathlib; pathlib.Path(sys.argv[2]).with_suffix('.opened').write_text(' '.join(sys.argv[1:]))"
GOOD = "0123abcd-4567-attachment.zip"


def command(tmp_path) -> list[str]:
    script = tmp_path / "fake_playwright.py"
    script.write_text(FAKE, encoding="utf-8")
    return [sys.executable, str(script)]


def test_only_a_local_app_can_open_a_window(tmp_path):
    assert blocker("127.0.0.1") == "" and blocker("localhost") == ""
    assert "only works when the app listens on this machine" in blocker("0.0.0.0")


def test_the_viewer_gets_the_trace_path(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    (results / GOOD).write_bytes(b"zip")
    path = open_trace(results, f"/runs/r1/allure-results/{GOOD}", command(tmp_path))
    marker = path.with_suffix(".opened")
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.parent == (tmp_path / "trace-viewer").resolve() and path.suffix == ".zip"
    assert path.read_bytes() == b"zip" and marker.read_text() == f"show-trace {path}"


def test_an_unknown_type_trace_is_opened_under_a_zip_name(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    (results / "0123abcd-4567-attachment.attach").write_bytes(b"zip")
    path = open_trace(results, "/runs/r1/allure-results/0123abcd-4567-attachment.attach", command(tmp_path))
    assert path.name == "0123abcd-4567-attachment.zip" and path.read_bytes() == b"zip"


@pytest.mark.parametrize("source", ["../secret.zip", "evil.zip", "0123abcd-4567-attachment.png", "", "a/../b.zip",
                                    "0123abcd-4567-attachment.zip/../../x"])
def test_only_this_runs_trace_files_are_opened(tmp_path, source):
    results = tmp_path / "allure-results"
    results.mkdir()
    (tmp_path / "secret.zip").write_bytes(b"zip")
    (results / "evil.zip").write_bytes(b"zip")
    with pytest.raises(ValueError):
        open_trace(results, source, command(tmp_path))


def test_a_missing_file_and_a_missing_program_are_explained(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    with pytest.raises(ValueError, match="not there"):
        open_trace(results, GOOD, command(tmp_path))
    (results / GOOD).write_bytes(b"zip")
    with pytest.raises(ValueError, match="Could not start"):
        open_trace(results, GOOD, [str(Path(tmp_path) / "no-such-program")])
