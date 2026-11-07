"""A test's whole story in one place: every result, and everything that happened to it, newest first.

History, trends, compare, flaky tests, heals and quarantine each show a slice of a test's life on their own screen.
The timeline puts them in one list: each run's result (environment, browser, outcome, time, the step it failed on
and why, retries), the heals and identity fallbacks it needed in that run, when it was quarantined or released and
why, and when the test case was saved. A summary on top says how it is doing: pass rate, changes of result, flaky
or not, how long it takes and whether that is creeping up, and its quarantine. That is what deciding "fix it or
quarantine it" needs, without five screens.

Pure: it takes the runs (newest first, from the results index), the audit log entries and the quarantine list.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from utils.quarantine import base_id
from webapp.services.heals import read_records
from webapp.services.run_insights import FAILING, PASSING, TestTrend

RECENT_RUNS = 50
AUDIT_ACTIONS = ("quarantine.add", "quarantine.release", "testcase.save", "testcase.delete")


@dataclass
class Entry:
    at: str
    kind: str  # result | heal | fallback | drift | quarantine | release | edit
    title: str
    detail: str = ""
    tone: str = ""  # passed | failed | warn | info
    run_id: str = ""
    env: str = ""
    browser: str = ""
    outcome: str = ""
    seconds: float | None = None
    who: str = ""


@dataclass
class Timeline:
    test_id: str
    system: str
    entries: list[Entry] = field(default_factory=list)  # newest first
    outcomes: list[str] = field(default_factory=list)  # oldest -> newest, results only
    durations: list[float] = field(default_factory=list)  # oldest -> newest, passed results
    quarantine: dict = field(default_factory=dict)  # {reason, since} when quarantined
    state: str = "none"  # flaky | failing | fixed | broke | passing | none (run_insights.TestTrend)
    flips: int = 0
    flaky: bool = False

    @property
    def results(self) -> int:
        return len(self.outcomes)

    @property
    def pass_rate(self) -> int | None:
        decided = [o for o in self.outcomes if o in PASSING | FAILING]
        return round(100 * sum(o in PASSING for o in decided) / len(decided)) if decided else None

    @property
    def typical_seconds(self) -> float | None:
        if not self.durations:
            return None
        ordered = sorted(self.durations)
        return round(ordered[len(ordered) // 2], 1)

    @property
    def slowing(self) -> bool:
        """The latest 5 passing runs took a quarter longer than the 5 before them."""
        d = self.durations
        if len(d) < 10:
            return False
        before, after = sum(d[-10:-5]) / 5, sum(d[-5:]) / 5
        return before > 0 and after > before * 1.25

    def to_dict(self) -> dict:
        return {"test_id": self.test_id, "system": self.system, "state": self.state, "flaky": self.flaky,
                "flips": self.flips, "results": self.results, "pass_rate": self.pass_rate,
                "typical_seconds": self.typical_seconds, "slowing": self.slowing, "quarantine": self.quarantine,
                "outcomes": self.outcomes, "entries": [asdict(e) for e in self.entries]}


def _failed_step(steps: list[dict]) -> str:
    failed = next((s for s in steps or [] if s.get("status") == "failed"), None)
    if not failed:
        return ""
    return f"step {failed.get('n')} of {failed.get('of')}: {failed.get('label', '')}".strip()


def _first_line(text: str, limit: int = 220) -> str:
    line = next((x.strip() for x in str(text or "").splitlines() if x.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _same_test(test_id: str, wanted: str) -> bool:
    return base_id(test_id) == base_id(wanted)


def build(test_id: str, system: str, runs, audit_entries=(), quarantine: dict | None = None,
          env: str | None = None) -> Timeline:
    """The timeline of one test case (a data-set case `TC-1[set]` belongs to `TC-1`) over the runs given."""
    tl = Timeline(test_id=base_id(test_id), system=system)
    entries: list[Entry] = []
    chronological: list[tuple[str, str, float, bool]] = []
    for run in list(runs)[:RECENT_RUNS]:
        if run.config.system != system or (env and run.config.env != env):
            continue
        mine = [r for r in run.results.values() if _same_test(r.test_id, test_id) and r.outcome != "running"]
        if not mine:
            continue
        at = run.finished_at or run.created_at
        for r in mine:
            failed = r.outcome in FAILING
            detail = " · ".join(x for x in (_failed_step(r.steps) if failed else "",
                                            _first_line(r.message) if failed or r.retries else "",
                                            f"passed after {r.retries} retr{'y' if r.retries == 1 else 'ies'}"
                                            if r.retries and r.outcome in PASSING else "") if x)
            label = r.test_id if r.test_id != tl.test_id else ""
            entries.append(Entry(at, "result", f"{r.outcome.capitalize()}" + (f" ({label})" if label else ""),
                                 detail, "passed" if r.outcome in PASSING else ("failed" if failed else "info"),
                                 run.id, run.config.env, r.browser, r.outcome, round(float(r.duration or 0), 1)))
            chronological.append((at, r.outcome, float(r.duration or 0), bool(r.retries)))
        for heal in read_records(run.results_dir, "heals"):
            if _same_test(str(heal.get("test_id", "")), test_id):
                where = f"{heal.get('screen')}.{heal.get('field')}"
                entries.append(Entry(str(heal.get("at") or at), "heal", f"Healed {where}",
                                     f"its locator found nothing; used {heal.get('new')} ({heal.get('score')}%)",
                                     "warn", run.id, run.config.env))
        for rec in read_records(run.results_dir, "identity"):
            if _same_test(str(rec.get("test_id", "")), test_id) and rec.get("outcome") in ("fallback", "drift",
                                                                                          "missing"):
                what = {"fallback": f"its locator found nothing; fallback {rec.get('locator')} was used",
                        "drift": "its locator finds an element that no longer looks like it",
                        "missing": "neither its locator nor its fallbacks found it"}[rec["outcome"]]
                entries.append(Entry(str(rec.get("at") or at), rec["outcome"],
                                     f"{rec.get('screen')}.{rec.get('field')}: {rec['outcome']}", what,
                                     "failed" if rec["outcome"] == "missing" else "warn", run.id, run.config.env))
    for item in audit_entries:
        action = item.get("action")
        if action not in AUDIT_ACTIONS:
            continue
        if action.startswith("quarantine") and _same_test(str(item.get("target", "")), test_id):
            added = action == "quarantine.add"
            entries.append(Entry(str(item.get("at", "")), "quarantine" if added else "release",
                                 "Quarantined" if added else "Released",
                                 str(item.get("reason") or ("its failures no longer fail runs" if added
                                                            else "its failures fail runs again")),
                                 "warn" if added else "info", who=str(item.get("user", ""))))
        elif action.startswith("testcase") and _same_test(str(item.get("test_id", "")), test_id):
            entries.append(Entry(str(item.get("at", "")), "edit",
                                 "Test case saved" if action == "testcase.save" else "Test case deleted",
                                 str(item.get("target", "")), "info", who=str(item.get("user", ""))))
    entries.sort(key=lambda e: (e.at, e.kind != "result"), reverse=True)
    tl.entries = entries
    chronological.sort(key=lambda x: x[0])
    tl.outcomes = [o for _, o, _, _ in chronological]
    tl.durations = [d for _, o, d, _ in chronological if o in PASSING and d > 0]
    trend = TestTrend(tl.test_id, "", outcomes=list(tl.outcomes), retried=[r for _, o, _, r in chronological
                                                                             if o in PASSING])
    tl.state, tl.flips, tl.flaky = trend.state, trend.flips, trend.flaky
    tl.quarantine = dict((quarantine or {}).get(tl.test_id) or {})
    return tl
