"""Journeys: the file, the status on an environment from runs, and coverage from steps and pages."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from webapp.services.journeys import (
    Journey, JourneyError, JourneyStore, coverage, slug, status, validate,
)
from webapp.services.run_manager import Run, RunConfig, TestResult

NOW = datetime(2026, 11, 6, 12, 0, 0)
JOURNEY = Journey(id="reg", name="Business registration", system="S", criticality="critical",
                  tests=["TC-1", "TC-2"])


def run(tmp_path, rid, when, results, env="dev", system="S", state="passed"):
    r = Run(id=rid, config=RunConfig(system=system, env=env, tags=["t"]), dir=tmp_path, status=state,
            created_at=when, started_at=when, finished_at=when)
    for i, (test_id, outcome, browser) in enumerate(results):
        r.results[f"n{i}-{test_id}-{browser}"] = TestResult(f"n{i}", test_id, browser, outcome, 1.0)
    return r


# ───── file ─────

def test_validate_cleans_and_refuses():
    j = validate({"name": "  Annual report  ", "system": "S", "tests": ["TC-1", " TC-1", "", "TC-2"],
                  "criticality": "HIGH", "owner": "x" * 100})
    assert (j.id, j.name, j.criticality, j.tests, len(j.owner)) == ("annual-report", "Annual report", "high",
                                                                   ["TC-1", "TC-2"], 80)
    for bad in ({}, {"name": "x"}, {"name": "x", "system": "../x"},
                {"name": "x", "system": "S", "criticality": "low"}, {"name": "x" * 81, "system": "S"},
                {"name": "x", "system": "S", "id": "Bad Id"}):
        with pytest.raises(JourneyError):
            validate(bad)
    assert slug("Résumé / filing!") == "r-sum-filing" and slug("!!!") == "journey"


def test_store_adds_replaces_orders_and_deletes(tmp_path):
    (tmp_path / "Pages").mkdir()  # a workspace: the edit history keeps versions next to Data, Pages and Tests
    store = JourneyStore(tmp_path)
    assert store.list() == []
    store.save(validate({"name": "Renewal", "system": "S", "criticality": "normal"}))
    store.save(validate({"name": "Registration", "system": "S", "criticality": "critical"}))
    store.save(validate({"name": "Other system", "system": "T"}))
    twin = store.save(validate({"name": "Renewal", "system": "S"}))
    assert twin.id == "renewal-2"  # a new journey with the same name gets its own id
    assert [j.name for j in store.list("S")] == ["Registration", "Renewal", "Renewal"]  # critical first
    renamed = validate({"name": "Renewal online", "system": "S", "tests": ["TC-9"]})
    store.save(renamed, replaces="renewal")
    assert store.get("renewal") is None and store.get("renewal-online").tests == ["TC-9"]
    with pytest.raises(JourneyError, match="already has the id"):
        store.save(validate({"name": "Registration", "system": "S"}), replaces="renewal-2")
    assert store.delete("renewal-2") and not store.delete("renewal-2")
    data = json.loads((tmp_path / "Data" / "journeys.json").read_text(encoding="utf-8"))
    assert {j["id"] for j in data["journeys"]} == {"registration", "renewal-online", "other-system"}
    from webapp.services import history
    assert history.versions(tmp_path / "Data" / "journeys.json")  # every change can be undone


def test_a_broken_entry_is_skipped_not_fatal(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "journeys.json").write_text(json.dumps({"journeys": [
        {"name": "Good", "system": "S"}, {"name": "", "system": "S"}, "junk"]}), encoding="utf-8")
    assert [j.name for j in JourneyStore(tmp_path).list()] == ["Good"]


# ───── status ─────

def test_never_run_then_passing_then_failing(tmp_path):
    assert status(JOURNEY, [], "dev", now=NOW).state == "not_run"
    both = [("TC-1", "passed", "chromium"), ("TC-2", "passed", "chromium")]
    runs = [run(tmp_path, "r1", "2026-11-05T10:00:00", both)]
    ok = status(JOURNEY, runs, "dev", now=NOW)
    assert (ok.state, ok.pass_rate, ok.runs_counted, ok.last_run) == ("passing", 100.0, 1, "r1")
    runs.append(run(tmp_path, "r2", "2026-11-06T09:00:00", [("TC-2", "failed", "chromium")], state="failed"))
    point = SimpleNamespace(failure=SimpleNamespace(short="Applicant > Zip (FILL)"))
    details = {"r2": {("TC-2", "chromium"): point}}
    bad = status(JOURNEY, runs, "dev", now=NOW, details_for=lambda r: details.get(r.id, {}))
    assert (bad.state, bad.where, bad.pass_rate, bad.last_run) == ("failing", "Applicant > Zip (FILL)", 50.0, "r2")
    assert {t.test_id: t.outcome for t in bad.tests} == {"TC-1": "passed", "TC-2": "failed"}


def test_partly_run_stale_and_other_environments(tmp_path):
    runs = [run(tmp_path, "r1", "2026-11-05T10:00:00", [("TC-1", "passed", "chromium")]),
            run(tmp_path, "r2", "2026-10-01T10:00:00",
                [("TC-1", "passed", "chromium"), ("TC-2", "passed", "chromium")], env="test"),
            run(tmp_path, "r3", "2026-11-06T10:00:00", [("TC-1", "failed", "chromium")], system="OTHER")]
    assert status(JOURNEY, runs, "dev", now=NOW).state == "incomplete"
    assert status(JOURNEY, runs, "test", now=NOW).state == "stale"  # over 7 days old
    assert status(JOURNEY, runs, "prod", now=NOW).state == "not_run"


def test_one_failing_browser_or_data_set_fails_the_test_in_that_run(tmp_path):
    runs = [run(tmp_path, "r1", "2026-11-06T10:00:00", [("TC-1[austin]", "passed", "chromium"),
                                                        ("TC-1[dallas]", "failed", "chromium"),
                                                        ("TC-2", "failed", "chromium"), ("TC-2", "passed", "firefox")])]
    result = status(JOURNEY, runs, "dev", now=NOW)
    assert result.state == "failing" and {t.test_id: t.outcome for t in result.tests} == {"TC-1": "failed",
                                                                                          "TC-2": "failed"}


def test_unfinished_runs_and_unrelated_results_are_ignored(tmp_path):
    runs = [run(tmp_path, "r1", "2026-11-06T10:00:00", [("TC-1", "failed", "chromium")], state="running"),
            run(tmp_path, "r2", "2026-11-06T09:00:00", [("TC-1", "passed", "c"), ("TC-2", "skipped", "c"),
                                                        ("TC-9", "failed", "c")])]
    result = status(JOURNEY, runs, "dev", now=NOW)
    assert result.state == "incomplete" and result.pass_rate == 100.0  # TC-2 only skipped; TC-9 is not ours


def test_an_empty_journey_is_never_run():
    assert status(Journey("e", "Empty", "S"), [], "dev", now=NOW).state == "not_run"


# ───── coverage ─────

def test_coverage_counts_fields_through_common_actions_reused_tests_and_overrides():
    tests = {"TC-1": [{"commonAction": "login"},
                      {"screen": "Applicant", "formData": {"Zip": "1", "City|": "x", "Gone": "y"}},
                      {"testCase": "TC-SHARED"}],
             "TC-2": [{"commonAction": "login", "override": "Login|Remember|yes"}],
             "TC-SHARED": [{"screen": "Review", "formData": {"Submit": "click"}}]}
    pages = {"Login": {"User", "Password", "Go", "Remember"}, "Applicant": {"Zip", "City", "State", "Phone"},
             "Review": {"Submit", "Back"}}
    actions = {"login": [{"screen": "Login", "formData": {"User": "u", "Password": "p", "Go": "click"}}]}
    journey = Journey("j", "J", "S", tests=["TC-1", "TC-2", "TC-404"])
    cov = coverage(journey, tests.get, pages.get, lambda name: actions.get(name, []))
    by = {s.screen: s for s in cov.screens}
    assert by["Login"].used == ["Go", "Password", "Remember", "User"] and by["Login"].percent == 100.0
    assert by["Applicant"].used == ["City", "Zip"] and by["Applicant"].percent == 50.0  # "Gone" is not on the page
    assert by["Review"].used == ["Submit"]
    assert cov.missing_tests == ["TC-404"] and cov.percent == 70.0  # 7 of 10 fields
    ghost = {"TC-X": [{"screen": "Ghost", "formData": {"a": 1}}]}
    nowhere = coverage(Journey("k", "K", "S", tests=["TC-X"]), ghost.get, pages.get, lambda n: [])
    assert nowhere.screens[0].total == 0 and nowhere.percent is None
