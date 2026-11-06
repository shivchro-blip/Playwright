"""Release evidence: a go / no-go decision for a release, with the evidence behind it, that people sign off.

A release check takes the journeys of a system (all of them, or the ones chosen) on one environment, looks at the
latest result of every test case that proves them (optionally only results since the release was deployed), and
applies a policy:

- a journey whose criticality blocks (critical and high by default) must be passing: a failing journey, one whose
  test cases have not all run, or one whose evidence is older than the allowed age, makes the release NO-GO;
- anything else that is not right (a failing normal journey, a quarantined test, a test that passed only on a retry)
  is listed as a warning, so nobody signs without seeing it.

The result is saved as a pack (reports/releases/<id>/pack.json) with a SHA-256 fingerprint of its content and an
HMAC signature made with this server's key, so a changed pack is detected. People then approve or reject it; each
sign-off records who, when, why, and the fingerprint they signed. Approving a NO-GO needs a written reason (an
accepted risk). The pack also renders as a self-contained HTML report to print or attach.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from webapp.services.journeys import CRITICALITY, FAILING, PASSING, STALE_DAYS, STATE_LABELS
from webapp.services.users import session_secret

SCHEMA = 1
ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9-]{1,40}$")


class ReleaseError(ValueError):
    """Shown to the user as is."""


@dataclass
class Policy:
    block: list[str] = field(default_factory=lambda: ["critical", "high"])
    max_age_days: int = STALE_DAYS
    since: str = ""  # ISO time: only results from runs started since then count

    @classmethod
    def from_dict(cls, data: dict | None) -> Policy:
        data = data or {}
        block = [c for c in data.get("block", ["critical", "high"]) if c in CRITICALITY]
        try:
            age = int(data.get("max_age_days", STALE_DAYS))
        except (TypeError, ValueError):
            raise ReleaseError("The evidence age must be a whole number of days.") from None
        if not 1 <= age <= 365:
            raise ReleaseError("The evidence age must be between 1 and 365 days.")
        since = str(data.get("since") or "").strip()
        if since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?", since):
            raise ReleaseError("'Results since' must look like 2026-11-06 or 2026-11-06T09:30.")
        return cls(block, age, since.replace(" ", "T"))

    def to_dict(self) -> dict:
        return {"block": list(self.block), "max_age_days": self.max_age_days, "since": self.since}


# ───────────── deciding ─────────────

def decide(journeys: list[dict], policy: Policy) -> tuple[str, list[str], list[str]]:
    """(decision "go" | "no-go", blocking reasons, warnings) for the journey entries of a pack."""
    reasons, warnings = [], []
    if not journeys:
        return "no-go", ["No journeys were checked: there is no evidence for this release."], []
    for j in journeys:
        name, blocking, tests = j["name"], j["criticality"] in policy.block, j["tests"]
        ran = [t for t in tests if t["outcome"]]
        if j["state"] == "failing":
            failing = [t["test_id"] for t in tests if t["outcome"] in FAILING]
            text = f"{name} ({j['criticality']}) is failing: {', '.join(failing)}" + (
                f" at {j['where']}" if j.get("where") else "")
            (reasons if blocking else warnings).append(text)
        elif j["state"] in ("not_run", "incomplete"):
            text = (f"{name} ({j['criticality']}) has no evidence: {len(ran)} of {len(tests)} test cases ran"
                    + (f" since {policy.since}" if policy.since else ""))
            (reasons if blocking else warnings).append(text)
        elif j["state"] == "stale":
            text = f"{name} ({j['criticality']}) was last proven more than {policy.max_age_days} days ago"
            (reasons if blocking else warnings).append(text)
        for t in tests:
            if t.get("quarantined"):
                warnings.append(f"{name}: {t['test_id']} is quarantined, so a failure there does not count")
            elif t["outcome"] in PASSING and t.get("retries"):
                warnings.append(f"{name}: {t['test_id']} passed only after {t['retries']} retr"
                                f"{'y' if t['retries'] == 1 else 'ies'} (flaky)")
            if t["outcome"] in PASSING and t.get("healed"):
                warnings.append(f"{name}: {t['test_id']} passed with {t['healed']} healed locator"
                                f"{'s' if t['healed'] != 1 else ''}: review them before trusting this result")
    return ("no-go" if reasons else "go"), reasons, warnings


# ───────────── signing ─────────────

def canonical(pack: dict) -> bytes:
    body = {k: v for k, v in pack.items() if k not in ("fingerprint", "signature")}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fingerprint(pack: dict) -> str:
    return hashlib.sha256(canonical(pack)).hexdigest()


class ReleaseStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._key: bytes | None = None

    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = session_secret(self.folder / ".release-key").encode()
        return self._key

    def sign(self, pack: dict) -> dict:
        pack["fingerprint"] = fingerprint(pack)
        pack["signature"] = hmac.new(self.key, canonical(pack), hashlib.sha256).hexdigest()
        return pack

    def verify(self, pack: dict) -> dict:
        expected_fp = fingerprint(pack)
        expected_sig = hmac.new(self.key, canonical(pack), hashlib.sha256).hexdigest()
        return {"fingerprint_ok": hmac.compare_digest(expected_fp, str(pack.get("fingerprint", ""))),
                "signature_ok": hmac.compare_digest(expected_sig, str(pack.get("signature", "")))}

    def _dir(self, rid: str) -> Path:
        if not ID_RE.match(rid or ""):
            raise ReleaseError("No such release check.")
        return self.folder / rid

    def save(self, pack: dict) -> dict:
        folder = self._dir(pack["id"])
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "pack.json").write_text(json.dumps(self.sign(pack), indent=2, ensure_ascii=False),
                                          encoding="utf-8")
        return pack

    def get(self, rid: str) -> dict | None:
        try:
            return json.loads((self._dir(rid) / "pack.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, ReleaseError):
            return None

    def list(self, system: str | None = None) -> list[dict]:
        out = []
        for path in sorted(self.folder.glob("*/pack.json"), reverse=True) if self.folder.is_dir() else []:
            try:
                pack = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if system is None or pack.get("system") == system:
                out.append(pack)
        return out

    def signoffs(self, rid: str) -> list[dict]:
        try:
            data = json.loads((self._dir(rid) / "signoffs.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, ReleaseError):
            return []
        return data if isinstance(data, list) else []

    def add_signoff(self, rid: str, user: str, role: str, decision: str, comment: str) -> dict:
        pack = self.get(rid)
        if pack is None:
            raise ReleaseError("No such release check.")
        if decision not in ("approve", "reject"):
            raise ReleaseError("A sign-off approves or rejects.")
        comment = (comment or "").strip()
        if decision == "approve" and pack["decision"] != "go" and len(comment) < 10:
            raise ReleaseError("This release is NO-GO. Approving it anyway means accepting the risk: write why "
                               "(at least 10 characters).")
        if decision == "reject" and not comment:
            raise ReleaseError("Say why you reject it.")
        if not self.verify(pack)["fingerprint_ok"]:
            raise ReleaseError("The evidence was changed after it was created; it cannot be signed.")
        entry = {"user": user, "role": role, "decision": decision, "comment": comment[:1000],
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "fingerprint": pack["fingerprint"]}
        signoffs = self.signoffs(rid) + [entry]
        (self._dir(rid) / "signoffs.json").write_text(json.dumps(signoffs, indent=2, ensure_ascii=False),
                                                      encoding="utf-8")
        return entry


def outcome(pack: dict, signoffs: list[dict]) -> str:
    """approved | approved-with-risk | rejected | awaiting"""
    if any(s["decision"] == "reject" for s in signoffs):
        return "rejected"
    if any(s["decision"] == "approve" for s in signoffs):
        return "approved" if pack["decision"] == "go" else "approved-with-risk"
    return "awaiting"


OUTCOME_LABELS = {"approved": "approved", "approved-with-risk": "approved with accepted risk",
                  "rejected": "rejected", "awaiting": "awaiting sign-off"}


# ───────────── the service ─────────────

class ReleaseService:
    def __init__(self, store: ReleaseStore, journeys, runs, quarantine, public_url: str = ""):
        self.store, self.journeys, self.runs, self.quarantine = store, journeys, runs, quarantine
        self.public_url = public_url.rstrip("/")

    def create(self, name: str, system: str, env: str, journey_ids: list[str] | None, policy: Policy,
               user: str) -> dict:
        name = (name or "").strip()
        if not name or len(name) > 80:
            raise ReleaseError("Name the release (at most 80 characters), for example 'Release 4.2' or 'Sprint 31'.")
        if env not in self.journeys.systems.environments(system):
            raise ReleaseError(f"{system} has no environment {env!r} in systemData.json.")
        evaluated = self.journeys.evaluate(system, env, journey_ids, policy.since or None, policy.max_age_days)
        quarantined = self.quarantine.all()
        entries, run_ids = [], set()
        for journey, st, cov in evaluated:
            tests = []
            for t in st.tests:
                tests.append({"test_id": t.test_id, "outcome": t.outcome, "run_id": t.run_id, "at": t.at,
                              "browser": t.browser, "where": t.where, "retries": t.retries, "healed": t.healed,
                              "message": t.message, "quarantined": t.test_id in quarantined})
                if t.run_id:
                    run_ids.add(t.run_id)
            entries.append({"id": journey.id, "name": journey.name, "criticality": journey.criticality,
                            "owner": journey.owner, "state": st.state, "label": STATE_LABELS[st.state],
                            "pass_rate": st.pass_rate, "where": st.where, "coverage": cov.percent,
                            "tests": tests})
        decision, reasons, warnings = decide(entries, policy)
        runs = []
        for rid in sorted(run_ids):
            run = self.runs.get(rid)
            if run:
                runs.append({"id": run.id, "status": run.status, "started_at": run.started_at,
                             "finished_at": run.finished_at, "tags": list(run.config.tags),
                             "browsers": list(run.config.browsers), "runner": run.claimed_by or "this machine"})
        stamp = time.strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "release"
        pack = {"schema": SCHEMA, "id": f"{stamp}-{slug}", "name": name, "system": system, "env": env,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "created_by": user, "policy": policy.to_dict(),
                "decision": decision, "reasons": reasons, "warnings": warnings, "journeys": entries, "runs": runs}
        while self.store.get(pack["id"]):
            pack["id"] = f"{stamp}-{slug}-{int(time.time() * 1000) % 1000}"
        return self.store.save(pack)

    def summary(self, pack: dict) -> dict:
        signoffs = self.store.signoffs(pack["id"])
        return {**{k: pack[k] for k in ("id", "name", "system", "env", "created_at", "created_by", "decision")},
                "reasons": pack["reasons"], "warnings": pack["warnings"], "outcome": outcome(pack, signoffs),
                "signoffs": signoffs, "fingerprint": pack.get("fingerprint", ""),
                "journeys": len(pack["journeys"]), **self.store.verify(pack)}

    def report_html(self, pack: dict) -> str:
        return render_html(pack, self.store.signoffs(pack["id"]), self.store.verify(pack), self.public_url)


# ───────────── the report ─────────────

def _e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_html(pack: dict, signoffs: list[dict], verified: dict, public_url: str = "") -> str:
    state = outcome(pack, signoffs)
    go = pack["decision"] == "go"
    ok = verified.get("fingerprint_ok") and verified.get("signature_ok")
    rows = []
    for j in pack["journeys"]:
        tests = "".join(
            f"<tr><td class='mono'>{_e(t['test_id'])}"
            f"{' <span class=tag>quarantined</span>' if t['quarantined'] else ''}"
            f"{' <span class=tag>retried</span>' if t['retries'] and not t['quarantined'] else ''}</td>"
            f"<td class='{_e(t['outcome'] or 'none')}'>{_e(t['outcome'] or 'never run')}</td>"
            f"<td>{_e(t['where'])}</td><td class='mono'>{_e(t['run_id'])}</td><td class='mono'>{_e(t['at'])}</td></tr>"
            for t in j["tests"])
        rows.append(
            f"<section class='journey'><h3>{_e(j['name'])} <span class='crit {_e(j['criticality'])}'>"
            f"{_e(j['criticality'])}</span> <span class='state {_e(j['state'])}'>{_e(j['label'])}</span></h3>"
            f"<p class='muted'>Owner: {_e(j['owner'] or 'not set')} · Coverage: "
            f"{_e('-' if j['coverage'] is None else str(j['coverage']) + '% of fields')} · Recent pass rate: "
            f"{_e('-' if j['pass_rate'] is None else str(j['pass_rate']) + '%')}</p>"
            f"<table><thead><tr><th>Test case</th><th>Latest result</th><th>Where it failed</th><th>Run</th>"
            f"<th>When</th></tr></thead><tbody>{tests}</tbody></table></section>")
    signs = "".join(
        f"<tr><td>{_e(s['user'])} ({_e(s['role'])})</td><td class='{_e(s['decision'])}'>{_e(s['decision'])}</td>"
        f"<td>{_e(s['comment'])}</td><td class='mono'>{_e(s['at'])}</td>"
        f"<td class='mono'>{_e(s['fingerprint'][:16])}</td></tr>" for s in signoffs) or \
        "<tr><td colspan=5 class='muted'>No sign-offs yet.</td></tr>"
    runs = "".join(
        f"<tr><td class='mono'>{_e(r['id'])}</td><td>{_e(r['status'])}</td><td class='mono'>{_e(r['started_at'])}</td>"
        f"<td>{_e(', '.join(r['tags']))}</td><td>{_e(', '.join(r['browsers']))}</td><td>{_e(r['runner'])}</td></tr>"
        for r in pack["runs"]) or "<tr><td colspan=6 class='muted'>No runs.</td></tr>"
    reasons = "".join(f"<li>{_e(r)}</li>" for r in pack["reasons"])
    warnings = "".join(f"<li>{_e(w)}</li>" for w in pack["warnings"])
    policy = pack["policy"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Release evidence: {_e(pack['name'])}</title>
<style>
body{{font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#101828;margin:0;background:#f2f4f7}}
main{{max-width:1040px;margin:24px auto;background:#fff;border:1px solid #e2e6ec;border-radius:12px;padding:28px 32px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px}} h3{{font-size:15px;margin:18px 0 4px}}
.muted{{color:#5d6679}} .mono{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}}
.banner{{border-radius:10px;padding:14px 18px;margin:16px 0;font-size:16px;font-weight:600}}
.banner.go{{background:#e3f4ea;color:#0e5a35}} .banner.no-go{{background:#fdecea;color:#b42318}}
table{{border-collapse:collapse;width:100%;margin:6px 0}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #eef1f5;vertical-align:top}}
th{{font-size:12px;color:#5d6679;font-weight:500}}
.passed,.approve{{color:#0e5a35}} .failed,.error,.reject{{color:#b42318}} .none{{color:#5d6679}}
.crit,.state,.tag{{font-size:11px;padding:1px 7px;border-radius:999px;background:#eef1f5;font-weight:500}}
.crit.critical{{background:#fdecea;color:#b42318}} .crit.high{{background:#fff7e8;color:#6b3d00}}
.state.passing{{background:#e3f4ea;color:#0e5a35}} .state.failing{{background:#fdecea;color:#b42318}}
.state.stale,.state.incomplete{{background:#fff7e8;color:#6b3d00}}
footer{{margin-top:28px;font-size:12px;color:#5d6679;border-top:1px solid #e2e6ec;padding-top:12px}}
@media print{{body{{background:#fff}} main{{border:0;margin:0;max-width:none}}}}
</style></head><body><main>
<div class="muted">Release evidence · {_e(pack['system'])} / {_e(pack['env'])}</div>
<h1>{_e(pack['name'])}</h1>
<div class="muted">Checked {_e(pack['created_at'])} by {_e(pack['created_by'])} · {len(pack['journeys'])} journeys ·
sign-off: <b>{_e(OUTCOME_LABELS[state])}</b></div>
<div class="banner {'go' if go else 'no-go'}">{'GO: every blocking journey is proven working.' if go else
'NO-GO: ' + str(len(pack['reasons'])) + ' blocking problem' + ('s' if len(pack['reasons']) != 1 else '') + '.'}</div>
{f'<h2>Why it is NO-GO</h2><ul>{reasons}</ul>' if reasons else ''}
{f'<h2>Warnings</h2><ul>{warnings}</ul>' if warnings else ''}
<h2>Policy</h2>
<p>Blocking journeys: {_e(', '.join(policy['block']) or 'none')}. Evidence must be at most
{_e(policy['max_age_days'])} days old{_e(', from runs since ' + policy['since']) if policy['since'] else ''}.
Quarantined test cases, tests that passed only after a retry and tests with healed locators are listed as warnings.</p>
<h2>Journeys</h2>{''.join(rows) or '<p class="muted">None.</p>'}
<h2>Runs used as evidence</h2><table><thead><tr><th>Run</th><th>Status</th><th>Started</th><th>Tags</th>
<th>Browsers</th><th>Ran on</th></tr></thead><tbody>{runs}</tbody></table>
<h2>Sign-off</h2><table><thead><tr><th>Who</th><th>Decision</th><th>Why</th><th>When</th><th>Signed evidence</th></tr>
</thead><tbody>{signs}</tbody></table>
<footer>Evidence fingerprint (SHA-256): <span class="mono">{_e(pack.get('fingerprint', ''))}</span><br>
{'Verified: the evidence is unchanged since it was created and was signed by this AutoPlay server.' if ok else
'WARNING: this evidence does not match its fingerprint or signature. It was changed after it was created.'}
{f"<br>Run reports: {_e(public_url)}/runs/&lt;run&gt;/report.html" if public_url else ''}</footer>
</main></body></html>"""
