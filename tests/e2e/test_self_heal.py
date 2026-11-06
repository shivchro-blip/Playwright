"""Self-healing locators: a renamed id is healed during the run (opt-in), recorded, reviewed and accepted."""
from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.test_runs import outcomes, run_and_wait

SUITE = Path("Tests") / "SMOKE" / "MasterRegressionSuite"


def add_redesigned_page(workspace: Path, zip_type: str = "text") -> None:
    """A page whose zip field still uses the old id (#zip-code); the live page now calls it #applicantZipCode."""
    (workspace / "Pages" / "SMOKE" / "Redesigned.json").write_text(json.dumps({
        "path": "/renamed.html",
        "fields": {"zip": {"locator": "#zip-code", "type": zip_type},
                   "phone": {"locator": "#contactPhone", "type": "text"}}}), encoding="utf-8")
    (workspace / SUITE / "Heal.json").write_text(json.dumps({"testCases": [{
        "testCaseID": "TC-HEAL-001", "description": "Fills a field whose id changed.", "system": "SMOKE",
        "module": "heal", "environment": ["dev"], "tags": ["heal"],
        "steps": [{"screen": "Redesigned", "formData": {"phone": "5551234567", "zip": "78701"}}]}]}),
        encoding="utf-8")


def heals(run) -> list[dict]:
    return [json.loads(line) for f in run.results_dir.glob("autoplay-heals-*.jsonl")
            for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_without_self_heal_the_renamed_field_fails(workspace: Path, tmp_path: Path):
    add_redesigned_page(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["heal"], options={"headlessMode": True})
    assert outcomes(run) == {"TC-HEAL-001": "failed"} and heals(run) == []


def test_self_heal_finds_the_renamed_field_and_records_it(workspace: Path, tmp_path: Path):
    add_redesigned_page(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["heal"], options={"headlessMode": True, "selfHeal": True})
    assert outcomes(run) == {"TC-HEAL-001": "passed"}, "\n".join(run.log[-40:])
    found = heals(run)
    assert len(found) == 1
    heal = found[0]
    assert (heal["screen"], heal["field"], heal["old"], heal["new"]) == (
        "Redesigned", "zip", {"locator": "#zip-code"}, {"locator": "#applicantZipCode"})
    assert heal["score"] >= 60 and heal["test_id"] == "TC-HEAL-001"
    assert any("[heal] zip on Redesigned" in line for line in run.log)
    profiles = [json.loads(line) for f in run.results_dir.glob("autoplay-profiles-*.jsonl")
                for line in f.read_text(encoding="utf-8").splitlines()]
    phone = next(p for p in profiles if p["field"] == "phone")
    assert phone["profile"]["id"] == "contactPhone" and phone["profile"]["label"] == "Contact phone"


def test_a_different_kind_of_element_is_never_used(workspace: Path, tmp_path: Path):
    add_redesigned_page(workspace, zip_type="button")  # the field is a button: the zip text box must not be used
    run = run_and_wait(workspace, tmp_path, tags=["heal"], options={"headlessMode": True, "selfHeal": True})
    assert outcomes(run) == {"TC-HEAL-001": "failed"} and heals(run) == []


def test_heals_are_reviewed_in_history_accept_updates_the_page(workspace: Path, tmp_path: Path):
    from playwright.sync_api import expect, sync_playwright

    from tests.e2e.test_ui import running_app

    add_redesigned_page(workspace)
    run = run_and_wait(workspace, tmp_path, tags=["heal"], options={"headlessMode": True, "selfHeal": True})
    assert next(iter(run.results.values())).healed == 1
    assert "1 locator was healed during this run" in "\n".join(run.log)
    profiles = json.loads((workspace / "Data" / "locatorProfiles" / "SMOKE.json").read_text(encoding="utf-8"))
    assert profiles["Redesigned"]["phone"]["id"] == "contactPhone"
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        expect(page.locator("tbody tr", has_text="Healed").first).to_be_visible()  # the result says so in Control
        page.get_by_role("tab", name="History").click()
        heal = page.get_by_test_id("heal-Redesigned-zip")
        expect(heal).to_contain_text("#zip-code")
        expect(heal).to_contain_text("#applicantZipCode")
        heal.get_by_test_id("heal-accept-zip").click()
        expect(page.get_by_text("Redesigned.zip now uses")).to_be_visible()
        expect(page.get_by_test_id("heals")).to_have_count(0)  # nothing left to review
        browser.close()
    saved = json.loads((workspace / "Pages" / "SMOKE" / "Redesigned.json").read_text(encoding="utf-8"))
    assert saved["fields"]["zip"] == {"locator": "#applicantZipCode", "type": "text"}
    again = run_and_wait(workspace, tmp_path, tags=["heal"], options={"headlessMode": True})  # no healing needed now
    assert outcomes(again) == {"TC-HEAL-001": "passed"}


def test_a_rejected_heal_is_not_used_again(workspace: Path, tmp_path: Path):
    from webapp.services.heals import HealService
    from webapp.services.pom_service import PomService
    from webapp.services.run_manager import RunManager
    import sys

    add_redesigned_page(workspace)
    run_and_wait(workspace, tmp_path, tags=["heal"], options={"headlessMode": True, "selfHeal": True})
    manager = RunManager(repo_root=Path(__file__).resolve().parents[2], workspace=workspace,
                         runs_dir=tmp_path / "runs", python_exe=sys.executable)
    service = HealService(workspace, manager, PomService(workspace, workspace))
    pending = service.pending("SMOKE")
    assert [(p.screen, p.field, p.count) for p in pending] == [("Redesigned", "zip", 1)]
    service.reject(pending[0].key, "tester")
    assert service.pending("SMOKE") == []
    rerun = run_and_wait(workspace, tmp_path, tags=["heal"], options={"headlessMode": True, "selfHeal": True})
    assert outcomes(rerun) == {"TC-HEAL-001": "failed"} and heals(rerun) == []  # the rejected locator is not used
