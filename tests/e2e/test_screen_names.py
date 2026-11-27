"""A screen is found by file name alone, so a name two page files share must fail the step that uses it, clearly,
instead of silently running against whichever file the OS listed last. A failing step also names its screen."""
from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.test_runs import outcomes, run_and_wait


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def add_test(workspace: Path, screen: str, form_data: dict, tag: str) -> None:
    write(workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / f"{tag}.json", {"testCases": [
        {"testCaseID": f"TC-{tag}", "system": "SMOKE", "module": "names", "tags": [tag], "steps": [
            {"screen": screen, "formData": form_data}]}]})


def test_two_page_files_with_one_name_fail_the_step_and_name_both_files(workspace: Path, tmp_path: Path):
    pages = workspace / "Pages" / "SMOKE"
    for app, field in (("AppOne", "searchBox"), ("AppTwo", "other")):
        write(pages / app / "Shared.json",
              {"path": "/index.html", "fields": {field: {"locator": "#q", "type": "text"}}})
    add_test(workspace, "Shared", {"searchBox": "hello"}, "dup")
    run = run_and_wait(workspace, tmp_path, tags=["dup"])
    log = "\n".join(run.log)
    assert run.status == "failed", log
    assert outcomes(run) == {"TC-dup": "failed"}
    assert "Screen 'Shared' is defined by 2 page files" in log
    assert "AppOne" in log and "AppTwo" in log and "must be unique" in log


def test_a_duplicate_nobody_uses_does_not_break_other_tests(workspace: Path, tmp_path: Path):
    pages = workspace / "Pages" / "SMOKE"
    write(pages / "AppOne" / "Unused.json", {"fields": {}})
    write(pages / "AppTwo" / "Unused.json", {"fields": {}})
    run = run_and_wait(workspace, tmp_path, tags=["smoke"])
    assert run.status == "passed", "\n".join(run.log[-60:])


def test_the_same_screen_name_in_one_folder_per_page_still_runs(workspace: Path, tmp_path: Path):
    pages = workspace / "Pages" / "SMOKE"
    write(pages / "AppOne" / "OnlyHere.json",
          {"path": "/index.html", "fields": {"searchBox": {"locator": "#q", "type": "text"}}})
    add_test(workspace, "OnlyHere", {"searchBox": "hello"}, "single")
    run = run_and_wait(workspace, tmp_path, tags=["single"])
    assert run.status == "passed", "\n".join(run.log[-60:])


def test_an_unknown_field_names_the_screen_and_suggests_the_field_that_was_meant(workspace: Path, tmp_path: Path):
    add_test(workspace, "SmokeHome", {"searchBx": "hello"}, "typo")
    run = run_and_wait(workspace, tmp_path, tags=["typo"])
    log = "\n".join(run.log)
    assert run.status == "failed", log
    assert "No Field Config for: searchBx" in log
    assert "Did you mean: searchBox" in log
    assert "[Current Page = SmokeHome http://127.0.0.1" in log  # the screen and the page, no longer blank
