"""Release evidence: the policy, the decision and its reasons, signing and tamper detection, sign-offs, the report."""
from __future__ import annotations

import json

import pytest

from webapp.services.releases import (
    Policy, ReleaseError, ReleaseStore, decide, fingerprint, outcome, render_html,
)


def result(test_id, outcome_="passed", retries=0, quarantined=False, where=""):
    return {"test_id": test_id, "outcome": outcome_, "run_id": "r1" if outcome_ else "", "at": "2026-11-06T10:00:00",
            "browser": "chromium", "where": where, "retries": retries, "message": "", "quarantined": quarantined}


def journey(name, criticality, state, tests, where=""):
    return {"id": name.lower(), "name": name, "criticality": criticality, "owner": "", "state": state,
            "label": state, "pass_rate": 100.0, "where": where, "coverage": 50.0, "tests": tests}


def pack(decision="go", journeys=None, **extra):
    return {"schema": 1, "id": "20261106-100000-release-4-2", "name": "Release 4.2", "system": "S", "env": "test",
            "created_at": "2026-11-06T10:00:00", "created_by": "ana", "policy": Policy().to_dict(),
            "decision": decision, "reasons": [], "warnings": [], "journeys": journeys or [], "runs": [], **extra}


# ───── policy and decision ─────

def test_policy_defaults_and_refusals():
    p = Policy.from_dict({})
    assert (p.block, p.max_age_days, p.since) == (["critical", "high"], 7, "")
    assert Policy.from_dict({"block": ["critical", "bogus"], "since": "2026-11-06 09:30"}).to_dict() == {
        "block": ["critical"], "max_age_days": 7, "since": "2026-11-06T09:30"}
    for bad in ({"max_age_days": 0}, {"max_age_days": "x"}, {"since": "yesterday"}):
        with pytest.raises(ReleaseError):
            Policy.from_dict(bad)


def test_go_when_every_blocking_journey_passes():
    decision, reasons, warnings = decide([journey("Register", "critical", "passing", [result("TC-1")]),
                                          journey("Renew", "normal", "failing", [result("TC-2", "failed")])],
                                         Policy())
    assert decision == "go" and reasons == []
    assert warnings == ["Renew (normal) is failing: TC-2"]  # a normal journey warns, it does not block


def test_no_go_names_each_blocking_problem():
    decision, reasons, _ = decide([
        journey("Register", "critical", "failing", [result("TC-1", "failed")], where="Applicant > Zip (FILL)"),
        journey("Pay", "high", "incomplete", [result("TC-2"), result("TC-3", "")]),
        journey("Search", "critical", "stale", [result("TC-4")]),
        journey("Nothing", "high", "not_run", [result("TC-5", "")])], Policy(since="2026-11-06"))
    assert decision == "no-go"
    assert reasons == [
        "Register (critical) is failing: TC-1 at Applicant > Zip (FILL)",
        "Pay (high) has no evidence: 1 of 2 test cases ran since 2026-11-06",
        "Search (critical) was last proven more than 7 days ago",
        "Nothing (high) has no evidence: 0 of 1 test cases ran since 2026-11-06"]


def test_quarantine_and_retries_are_disclosed_and_an_empty_check_is_no_go():
    tests = [result("TC-1", "xfailed", quarantined=True), result("TC-2", retries=2)]
    _, _, warnings = decide([journey("Register", "critical", "passing", tests)], Policy())
    assert warnings == ["Register: TC-1 is quarantined, so a failure there does not count",
                        "Register: TC-2 passed only after 2 retries (flaky)"]
    assert decide([], Policy())[0] == "no-go"
    assert decide([journey("Low", "normal", "failing", [result("T", "failed")])], Policy(block=["critical", "high",
                                                                                              "normal"]))[0] == "no-go"


# ───── signing ─────

def test_a_saved_pack_verifies_and_any_change_is_detected(tmp_path):
    store = ReleaseStore(tmp_path)
    saved = store.save(pack(journeys=[journey("Register", "critical", "passing", [result("TC-1")])]))
    assert saved["fingerprint"] == fingerprint(saved) and len(saved["signature"]) == 64
    assert store.verify(store.get(saved["id"])) == {"fingerprint_ok": True, "signature_ok": True}
    path = tmp_path / saved["id"] / "pack.json"
    data = json.loads(path.read_text())
    data["journeys"][0]["state"] = "passing-but-edited"
    path.write_text(json.dumps(data))
    assert store.verify(store.get(saved["id"])) == {"fingerprint_ok": False, "signature_ok": False}
    data = json.loads(path.read_text())
    data["fingerprint"] = fingerprint(data)  # someone recomputes the hash after editing
    path.write_text(json.dumps(data))
    assert store.verify(store.get(saved["id"])) == {"fingerprint_ok": True, "signature_ok": False}
    other_server = ReleaseStore(tmp_path / "elsewhere")
    assert other_server.verify(saved)["signature_ok"] is False  # another server's key
    with pytest.raises(FileExistsError):
        store.save(pack())  # the same id is never overwritten


def test_ids_are_checked_and_listing_is_newest_first(tmp_path):
    store = ReleaseStore(tmp_path)
    store.save(pack(id="20261105-100000-a", system="S"))
    store.save(pack(id="20261106-100000-b", system="S"))
    store.save(pack(id="20261106-110000-c", system="T"))
    assert [p["id"] for p in store.list("S")] == ["20261106-100000-b", "20261105-100000-a"]
    for bad in ("../x", "nope", ""):
        assert store.get(bad) is None


# ───── sign-off ─────

def test_signoffs_record_who_why_and_what_they_signed(tmp_path):
    store = ReleaseStore(tmp_path)
    go = store.save(pack(id="20261106-100000-go"))
    assert outcome(go, []) == "awaiting"
    entry = store.add_signoff(go["id"], "ana", "editor", "approve", "")
    assert entry["fingerprint"] == go["fingerprint"] and outcome(go, store.signoffs(go["id"])) == "approved"
    with pytest.raises(ReleaseError, match="why you reject"):
        store.add_signoff(go["id"], "bo", "admin", "reject", " ")
    store.add_signoff(go["id"], "bo", "admin", "reject", "Pay journey looks wrong in UAT")
    assert outcome(go, store.signoffs(go["id"])) == "rejected"
    with pytest.raises(ReleaseError, match="approves or rejects"):
        store.add_signoff(go["id"], "x", "editor", "maybe", "")


def test_approving_a_no_go_needs_an_accepted_risk_and_edited_evidence_cannot_be_signed(tmp_path):
    store = ReleaseStore(tmp_path)
    bad = store.save(pack(id="20261106-100000-nogo", decision="no-go", reasons=["Register is failing"]))
    with pytest.raises(ReleaseError, match="accepting the risk"):
        store.add_signoff(bad["id"], "ana", "editor", "approve", "ok")
    store.add_signoff(bad["id"], "ana", "editor", "approve", "Hotfix 4.2.1 is scheduled for Friday")
    assert outcome(bad, store.signoffs(bad["id"])) == "approved-with-risk"
    path = tmp_path / bad["id"] / "pack.json"
    data = json.loads(path.read_text())
    data["decision"] = "go"
    path.write_text(json.dumps(data))
    with pytest.raises(ReleaseError, match="changed after it was created"):
        store.add_signoff(bad["id"], "bo", "admin", "approve", "")
    with pytest.raises(ReleaseError, match="No such"):
        store.add_signoff("20260101-000000-none", "bo", "admin", "approve", "")


# ───── report ─────

def test_the_report_shows_the_decision_reasons_evidence_and_escapes_everything():
    p = pack(decision="no-go", reasons=["Register (critical) is failing: TC-1"], warnings=["<script>x</script>"],
             journeys=[journey("Register <b>", "critical", "failing",
                               [result("TC-1", "failed", where="A > B (FILL)")])],
             runs=[{"id": "r1", "status": "failed", "started_at": "t", "finished_at": "t", "tags": ["smoke"],
                    "browsers": ["chromium"], "runner": "this machine"}], fingerprint="ab" * 32)
    page = render_html(p, [{"user": "ana", "role": "editor", "decision": "reject", "comment": "Fix it",
                            "at": "2026-11-06T11:00:00", "fingerprint": "ab" * 32}],
                       {"fingerprint_ok": True, "signature_ok": True}, "https://autoplay.example.com")
    assert "NO-GO: 1 blocking problem." in page and "Register (critical) is failing: TC-1" in page
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page and "Register &lt;b&gt;" in page
    assert "A &gt; B (FILL)" in page and "rejected" in page and "Fix it" in page and "ab" * 32 in page
    assert "Verified: the evidence is unchanged" in page
    tampered = render_html(pack(), [], {"fingerprint_ok": False, "signature_ok": False})
    assert "GO: every blocking journey" in tampered and "WARNING: this evidence does not match" in tampered


def test_only_editors_and_admins_sign_off():
    from webapp.services.users import allowed
    assert [r for r in ("viewer", "runner", "editor", "admin") if allowed(r, "signoff")] == ["editor", "admin"]
    assert allowed("runner", "run")  # a runner can check a release, not approve it
