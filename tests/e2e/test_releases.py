"""Release checks: GO / NO-GO from journeys and real runs, the signed evidence, sign-off, the report, the CI gate."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app

JOURNEYS = {"journeys": [
    {"id": "search", "name": "Search the site", "system": "SMOKE", "criticality": "critical",
     "tests": ["TC-SMOKE-001-Search", "TC-SMOKE-002-NotesAutoGen"]},
    {"id": "contact", "name": "Contact details", "system": "SMOKE", "criticality": "normal",
     "tests": ["TC-SMOKE-004-MissingElement"]}]}


def call(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_check_a_release_in_the_ui_sign_it_and_open_the_report(workspace: Path, tmp_path: Path):
    (workspace / "Data" / "journeys.json").write_text(json.dumps(JOURNEYS), encoding="utf-8")
    run_and_wait(workspace, tmp_path, tags=["smoke"])
    run_and_wait(workspace, tmp_path, tags=["missing"])
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Releases").click()
        expect(page.get_by_test_id("releases-empty")).to_be_visible()
        page.get_by_test_id("release-new").click()
        page.get_by_test_id("rc-name").fill("Release 4.2")
        page.get_by_test_id("rc-check").click()

        detail = page.get_by_test_id("release-detail")
        expect(detail.get_by_test_id("release-decision")).to_contain_text("GO")  # the critical journey passes
        expect(detail.get_by_test_id("release-decision")).not_to_contain_text("NO-GO")
        expect(detail.get_by_test_id("release-warnings")).to_contain_text("Contact details (normal) is failing")
        expect(detail.get_by_test_id("release-outcome")).to_contain_text("awaiting sign-off")
        expect(detail.get_by_test_id("release-verified")).to_contain_text("Evidence verified")

        detail.get_by_test_id("signoff-comment").fill("Checked on test, ship it")
        detail.get_by_test_id("signoff-sign").click()
        detail = page.get_by_test_id("release-detail")
        expect(detail.get_by_test_id("release-outcome")).to_contain_text("approved")
        expect(detail.get_by_test_id("release-signoff")).to_contain_text("Checked on test, ship it")

        with page.expect_popup() as popup:
            detail.get_by_test_id("release-report").click()
        report = popup.value
        expect(report.get_by_text("GO: every blocking journey is proven working.")).to_be_visible()
        expect(report.get_by_text("Checked on test, ship it")).to_be_visible()
        expect(report.get_by_text("Verified: the evidence is unchanged")).to_be_visible()
        browser.close()
    packs = list((tmp_path / "releases").glob("*/pack.json"))
    assert len(packs) == 1 and json.loads(packs[0].read_text())["decision"] == "go"


def test_the_api_is_a_release_gate_and_detects_edited_evidence(workspace: Path, tmp_path: Path):
    blocking = {"journeys": [{**JOURNEYS["journeys"][1], "criticality": "critical"}]}
    (workspace / "Data" / "journeys.json").write_text(json.dumps(blocking), encoding="utf-8")
    run_and_wait(workspace, tmp_path, tags=["missing"])
    with running_app(tmp_path) as url:
        status, check = call(f"{url}api/releases", "POST", {"name": "Release 4.3", "system": "SMOKE", "env": "dev"})
        assert status == 201 and check["decision"] == "no-go" and check["outcome"] == "awaiting"
        assert check["reasons"] == ["Contact details (critical) is failing: TC-SMOKE-004-MissingElement "
                                    "at SmokeHome > missing (FILL)"]
        assert check["fingerprint_ok"] and check["signature_ok"]
        rid = check["id"]
        assert call(f"{url}api/releases/{rid}/signoff", "POST", {"decision": "approve", "comment": "ok"})[0] == 400
        status, signed = call(f"{url}api/releases/{rid}/signoff", "POST",
                              {"decision": "approve", "comment": "Known issue, fix ships tomorrow"})
        assert status == 200 and signed["fingerprint"] == check["fingerprint"]
        listed = call(f"{url}api/releases?system=SMOKE")[1]
        assert listed[0]["outcome"] == "approved-with-risk"
        status, report = call(f"{url}api/releases/{rid}/report")
        assert status == 200 and "NO-GO: 1 blocking problem." in report

        assert call(f"{url}api/releases", "POST", {"name": "", "system": "SMOKE", "env": "dev"})[0] == 400
        assert call(f"{url}api/releases", "POST", {"name": "x", "system": "SMOKE", "env": "nowhere"})[0] == 400
        assert call(f"{url}api/releases/../../etc")[0] == 404

        pack_file = tmp_path / "releases" / rid / "pack.json"
        edited = json.loads(pack_file.read_text())
        edited["decision"] = "go"
        pack_file.write_text(json.dumps(edited))
        got = call(f"{url}api/releases/{rid}")[1]
        assert got["fingerprint_ok"] is False and got["signature_ok"] is False
        assert "WARNING: this evidence does not match" in call(f"{url}api/releases/{rid}/report")[1]
        assert call(f"{url}api/releases/{rid}/signoff", "POST", {"decision": "approve", "comment": "x"})[0] == 400


def test_only_results_since_the_deployment_count(workspace: Path, tmp_path: Path):
    (workspace / "Data" / "journeys.json").write_text(json.dumps(JOURNEYS), encoding="utf-8")
    run_and_wait(workspace, tmp_path, tags=["smoke"])
    with running_app(tmp_path) as url:
        status, check = call(f"{url}api/releases", "POST", {"name": "Next", "system": "SMOKE", "env": "dev",
                                                            "since": "2099-01-01"})
        assert status == 201 and check["decision"] == "no-go"  # nothing has run since the (future) deployment
        assert "has no evidence: 0 of 2 test cases ran since 2099-01-01" in check["reasons"][0]
