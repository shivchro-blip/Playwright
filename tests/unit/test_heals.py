"""Heal bookkeeping after a run, the review list, accept and reject, and how release checks disclose heals."""
from __future__ import annotations

import json
import sys

import pytest

from utils.self_heal import scoring_config, skip
from webapp.services.heals import HealService, after_run, read_records
from webapp.services.pom_service import PomService
from webapp.services.releases import Policy, decide
from webapp.services.run_manager import Run, RunConfig, RunManager, TestResult


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "Pages" / "S").mkdir(parents=True)
    (tmp_path / "Data").mkdir()
    (tmp_path / "Pages" / "S" / "Applicant.json").write_text(json.dumps(
        {"path": "/a", "fields": {"zip": {"locator": "#zip-code", "type": "text", "autoGen": "zip"}}}),
        encoding="utf-8")
    return tmp_path


def make_run(ws, rid, heals=(), profiles=()):
    runs = ws / "runs"
    run = Run(id=rid, config=RunConfig(system="S", env="dev", tags=["t"]), dir=runs / rid, status="passed",
              created_at=f"2026-11-06T1{rid[-1]}:00:00")
    run.results_dir.mkdir(parents=True)
    run.results["n"] = TestResult("n", "TC-1", "chromium", "passed", 1.0)
    with (run.results_dir / "autoplay-heals-1.jsonl").open("w") as f:
        for h in heals:
            f.write(json.dumps(h) + "\n")
    with (run.results_dir / "autoplay-profiles-1.jsonl").open("w") as f:
        for p in profiles:
            f.write(json.dumps(p) + "\n")
    (run.dir / "run.json").write_text(json.dumps(run.to_dict()))
    return run


HEAL = {"system": "S", "screen": "Applicant", "field": "zip", "test_id": "TC-1", "old": {"locator": "#zip-code"},
        "new": {"locator": "#applicantZipCode"}, "score": 99, "reason": "shares zip, code", "label": "Zip code"}


def test_after_run_marks_results_and_keeps_profiles(ws):
    run = make_run(ws, "r1", heals=[HEAL], profiles=[
        {"system": "S", "screen": "Applicant", "field": "phone", "profile": {"id": "phone", "label": "Phone"},
         "at": "t"}])
    (run.results_dir / "autoplay-heals-2.jsonl").write_text("{broken\n")
    assert after_run(run, ws) == 1 and run.results["n"].healed == 1
    saved = json.loads((ws / "Data" / "locatorProfiles" / "S.json").read_text())
    assert saved["Applicant"]["phone"] == {"id": "phone", "label": "Phone", "seen": "t", "run": "r1"}
    assert len(read_records(run.results_dir, "heals")) == 1  # the broken file is skipped


def test_pending_groups_accept_keeps_other_keys_and_reject_hides(ws):
    make_run(ws, "r1", heals=[HEAL])
    make_run(ws, "r2", heals=[HEAL, {**HEAL, "field": "city", "new": {"label": "City"}}])
    manager = RunManager(repo_root=ws, workspace=ws, runs_dir=ws / "runs", python_exe=sys.executable)
    service = HealService(ws, manager, PomService(ws, ws))
    items = {p.field: p for p in service.pending()}
    assert items["zip"].count == 2 and sorted(items["zip"].runs) == ["r1", "r2"] and items["zip"].tests == ["TC-1"]
    service.accept(items["zip"].key, "ana")
    page = json.loads((ws / "Pages" / "S" / "Applicant.json").read_text())
    assert page["fields"]["zip"] == {"type": "text", "autoGen": "zip", "locator": "#applicantZipCode"}
    with pytest.raises(ValueError, match="no field city"):
        service.accept(items["city"].key, "ana")  # the page has no city field
    service.reject(items["city"].key, "ana")
    assert service.pending() == []
    decisions = service.decisions()
    assert decisions["accepted"] == [items["zip"].key] and decisions["rejected"] == [items["city"].key]
    assert [entry["by"] for entry in decisions["log"]] == ["ana", "ana"]


def test_a_heal_already_in_the_page_is_not_listed(ws):
    make_run(ws, "r1", heals=[{**HEAL, "new": {"locator": "#zip-code"}}])
    manager = RunManager(repo_root=ws, workspace=ws, runs_dir=ws / "runs", python_exe=sys.executable)
    assert HealService(ws, manager, PomService(ws, ws)).pending() == []


def test_what_is_never_healed_and_how_the_profile_helps():
    assert skip({"locator": "#a"}, "wait") and skip({"tableId": "t"}, "click") and skip({"nested": [1]}, "click")
    assert skip({"locator": "[data-x='{value}']"}, "click") and skip({"locator": "#ENV.X"}, "fill")
    assert skip({"type": "text"}, "fill") and not skip({"locator": "#a"}, "fill")
    cfg = scoring_config({"locator": "#old", "type": "text"}, {"id": "zipCode", "name": "zip", "label": "Zip code",
                                                               "testid": "zip-input"})
    assert cfg["locator"] == "#old #zipCode [name='zip'] [data-testid='zip-input']" and cfg["label"] == "Zip code"


def test_release_checks_warn_about_healed_results():
    journey = {"name": "Register", "criticality": "critical", "state": "passing", "where": "",
               "tests": [{"test_id": "TC-1", "outcome": "passed", "retries": 0, "healed": 2, "quarantined": False}]}
    decision, reasons, warnings = decide([journey], Policy())
    assert decision == "go" and warnings == [
        "Register: TC-1 passed with 2 healed locators: review them before trusting this result"]
