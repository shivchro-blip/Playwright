"""The Markdown summary used for CI job summaries."""
from pathlib import Path

from webapp.services.run_artifacts import FailurePoint
from webapp.services.run_artifacts import TestDetails as Details  # pytest collects Test* names
from webapp.services.run_insights import build_markdown
from webapp.services.run_manager import Run, RunConfig
from webapp.services.run_manager import TestResult as Result


def _run(tmp_path: Path) -> Run:
    run = Run(id="r1", config=RunConfig(system="NYBE2", env="dev", tags=["smoke"], browsers=["chromium"]),
              dir=tmp_path, status="failed")
    run.results = {
        "a": Result("a", "TC-A", "chromium", "passed"),
        "b": Result("b", "TC-B", "chromium", "failed", message="boom\nsecond line"),
        "c": Result("c", "TC-C", "chromium", "failed", message="Locator `x` gone"),
    }
    return run


def test_summary_lists_counts_and_each_failure_with_where(tmp_path):
    details = {("TC-B", "chromium"): Details("TC-B", "chromium", "failed", message="Timeout 30000ms\ntrace",
                                                 failure=FailurePoint(screen="Financial", action="FILL",
                                                                      field_name="BankNme", step_no=7)),
               ("TC-C", "chromium"): Details("TC-C", "chromium", "failed",
                                                 failure=FailurePoint(screen="Financial", action="CLICK",
                                                                      field_name="Next"))}
    md = build_markdown(_run(tmp_path), details)
    assert md.startswith("## AutomateNY run r1: failed")
    assert "| Tests | passed | failed |" in md and "| 3 | 1 | 2 |" in md
    assert "- `TC-B` (chromium) failed at `Financial > BankNme (FILL)`: `Timeout 30000ms`" in md
    assert "By screen: `Financial` x2" in md


def test_summary_without_details_still_names_the_failures_and_caps_the_list(tmp_path):
    run = _run(tmp_path)
    md = build_markdown(run)
    assert "- `TC-B` (chromium): `boom`" in md and "- `TC-C` (chromium): `Locator 'x' gone`" in md  # no backticks leak
    capped = build_markdown(run, max_failures=1)
    assert "... and 1 more" in capped
