"""RunArtifacts: per-test failure details parsed from raw Allure results."""
from __future__ import annotations

import json
from pathlib import Path

from webapp.services.run_artifacts import RunArtifacts

UUID = "0123abcd-0000-4000-8000-00000000000"


def _att(results: Path, n: int, ext: str, body: bytes) -> str:
    source = f"{UUID}{n}-attachment{ext}"
    (results / source).write_bytes(body)
    return source


def _write_result(results: Path, data: dict, name: str = "r1") -> None:
    (results / f"{name}-result.json").write_text(json.dumps(data), encoding="utf-8")


def test_details_collects_error_nested_attachments_and_log(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    shot = _att(results, 1, ".png", b"\x89PNG")
    dom = _att(results, 2, ".html", b"<html></html>")
    action = _att(results, 3, ".txt", b'{"action": "fill"}')
    log = _att(results, 4, ".txt", b"line 1\nline 2\n")
    _write_result(results, {
        "name": "TC-1-Login [chromium]", "status": "failed",
        "statusDetails": {"message": "Timeout 5000ms", "trace": "Traceback ..."},
        "attachments": [{"name": "log", "source": log, "type": "text/plain"}],
        "steps": [{"name": "step", "attachments": [
            {"name": "Screenshot", "source": shot, "type": "image/png"},
            {"name": "DOM Snapshot", "source": dom, "type": "text/html"},
            {"name": "Action JSON", "source": action, "type": "text/plain"},
        ], "steps": []}],
    })
    details = RunArtifacts("/runs").details("run-1", results)
    item = details[("TC-1-Login", "chromium")]
    assert item.status == "failed" and item.message == "Timeout 5000ms" and item.trace == "Traceback ..."
    assert [a.url for a in item.by_kind("image")] == [f"/runs/run-1/allure-results/{shot}"]
    assert [a.name for a in item.by_kind("html")] == ["DOM Snapshot"]
    assert item.by_kind("text")[0].text == '{"action": "fill"}'
    assert item.log == "line 1\nline 2\n" and item.has_artifacts


def test_sources_outside_the_allure_pattern_are_ignored(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    _write_result(results, {"name": "TC-2 [firefox]", "status": "passed", "attachments": [
        {"name": "x", "source": "../secret.txt", "type": "text/plain"},
        {"name": "y", "source": "run.json", "type": "application/json"},
        {"name": "missing", "source": f"{UUID}9-attachment.png", "type": "image/png"},
    ]})
    item = RunArtifacts().details("r", results)[("TC-2", "firefox")]
    assert item.attachments == [] and not item.has_artifacts


def test_cache_refreshes_when_results_change(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    reader = RunArtifacts()
    assert reader.details("r", results) == {}
    _write_result(results, {"name": "TC-3 [webkit]", "status": "broken"})
    assert reader.details("r", results)[("TC-3", "webkit")].status == "broken"
    (results / "bad-result.json").write_text("{not json", encoding="utf-8")
    assert set(reader.details("r", results)) == {("TC-3", "webkit")}
    assert RunArtifacts().details("r", tmp_path / "missing") == {}


def _failing_result(results: Path, shot: str, dom: str) -> dict:
    return {"name": "TC-9 [chromium]", "status": "failed", "statusDetails": {"message": "boom"}, "steps": [
        {"name": "- Common Action: StartApp -", "status": "passed", "steps": []},
        {"name": "- Common Action: Apply -", "status": "failed", "steps": [
            {"name": "- Screen: Financial -", "status": "failed", "steps": [
                {"name": "1) CLICK - Next", "status": "passed", "steps": []},
                {"name": "7) FILL - BankNme", "status": "failed", "steps": [
                    {"name": "Checking visibility of element: 'BankNme'", "status": "failed", "attachments": [
                        {"name": "BankNme Visibility Error", "source": shot, "type": "image/png"},
                        {"name": "DOM Snapshot", "source": dom, "type": "text/html"}]}]}]}]}]}


def test_failure_point_names_the_screen_action_and_field_with_its_screenshot(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    shot = _att(results, 1, ".png", b"\x89PNG")
    dom = _att(results, 2, ".html", b"<html></html>")
    _write_result(results, _failing_result(results, shot, dom))
    point = RunArtifacts("/runs").details("run-1", results)[("TC-9", "chromium")].failure
    assert (point.common_action, point.screen, point.step_no, point.action, point.field_name) == \
        ("Apply", "Financial", 7, "FILL", "BankNme")
    assert point.crumbs == ["Common action Apply", "Screen Financial", "FILL BankNme (action 7)"]
    assert point.short == "Financial > BankNme (FILL)"
    assert point.screenshot.url == f"/runs/run-1/allure-results/{shot}"
    assert [a.name for a in point.attachments] == ["BankNme Visibility Error", "DOM Snapshot"]


def test_no_failure_point_for_passed_tests_or_failures_outside_a_screen(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    _write_result(results, {"name": "TC-1 [chromium]", "status": "passed", "steps": [
        {"name": "- Screen: Home -", "status": "passed", "steps": []}]}, "a")
    _write_result(results, {"name": "TC-2 [chromium]", "status": "failed", "steps": [
        {"name": "setup", "status": "failed", "steps": []}]}, "b")
    details = RunArtifacts().details("r", results)
    assert details[("TC-1", "chromium")].failure is None and details[("TC-2", "chromium")].failure is None
