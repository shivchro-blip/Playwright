"""Run what matters: pick the test cases worth running in a time budget, and say why each one is in or out.

Every test case of the system that can run is scored by its risk, from what the app already knows:
- the change: test cases a change reaches (webapp/services/impact.py), above all those it breaks;
- the business: test cases of critical and high journeys;
- the history on this environment: failing in its last run, flaky, never run here, or not run for days.

Test cases of critical journeys are always in, even past the budget: a plan never drops them silently (it says when
they alone go over). The rest are taken by risk per second of run time until the budget is spent; each one's time
is its median duration in recent runs (or the system's typical test time when it has none). What is left out is
listed with the reason, so a person can see what the plan did not run.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

FAILED = ("failed", "error")
DEFAULT_SECONDS = 30.0
STALE_DAYS = 7

WEIGHTS = {"breaks": 120, "impacted": 100, "critical": 90, "failing": 60, "never": 40, "high": 30, "flaky": 25,
           "journey": 10, "stale": 15}


@dataclass
class Planned:
    test_id: str
    score: int
    seconds: float
    reasons: list[str] = field(default_factory=list)
    must: bool = False  # a critical journey's test case: always in


@dataclass
class Plan:
    system: str
    env: str
    budget_seconds: float
    chosen: list[Planned] = field(default_factory=list)
    left_out: list[Planned] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(p.seconds for p in self.chosen)

    @property
    def test_ids(self) -> list[str]:
        return [p.test_id for p in self.chosen]

    def to_dict(self) -> dict:
        return {"system": self.system, "env": self.env, "budget_seconds": self.budget_seconds,
                "seconds": round(self.seconds, 1), "chosen": [asdict(p) for p in self.chosen],
                "left_out": [asdict(p) for p in self.left_out], "notes": self.notes,
                "test_ids": self.test_ids}


def _days_since(at: str, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(str(at)[:19])).total_seconds() / 86400
    except ValueError:
        return None


def plan(system: str, env: str, budget_seconds: float, tests: list[str], runs: list, *,
         journeys: list | None = None, impacted: dict[str, bool] | None = None,
         now: datetime | None = None) -> Plan:
    """`tests`: the system's runnable test case ids. `runs`: its finished runs on this environment, newest first
    (Run objects). `journeys`: Journey objects of the system. `impacted`: test id -> True when the change breaks it
    (False: only reaches it)."""
    now = now or datetime.now()
    impacted = impacted or {}
    result = Plan(system, env, float(budget_seconds))

    # history on this environment, per test: outcomes newest first, durations, when last run
    outcomes: dict[str, list[str]] = defaultdict(list)
    durations: dict[str, list[float]] = defaultdict(list)
    last_at: dict[str, str] = {}
    for run in runs:
        seen = set()
        for r in run.results.values():
            if r.outcome not in ("passed", *FAILED) or r.test_id in seen:
                continue
            seen.add(r.test_id)
            outcomes[r.test_id].append(r.outcome)
            if r.duration:
                durations[r.test_id].append(float(r.duration))
            last_at.setdefault(r.test_id, run.created_at)
    every = [d for ds in durations.values() for d in ds]
    typical = statistics.median(every) if every else DEFAULT_SECONDS

    crit: dict[str, str] = {}
    names: dict[str, str] = {}
    order = {"critical": 0, "high": 1, "normal": 2}
    for j in journeys or []:
        for t in j.tests:
            if t not in crit or order.get(j.criticality, 3) < order.get(crit[t], 3):
                crit[t], names[t] = j.criticality, j.name

    items: list[Planned] = []
    for tid in tests:
        reasons, score = [], 0
        if tid in impacted:
            score += WEIGHTS["breaks" if impacted[tid] else "impacted"]
            reasons.append("the change breaks it" if impacted[tid] else "the change reaches it")
        level = crit.get(tid)
        if level == "critical":
            score += WEIGHTS["critical"]
            reasons.append(f"critical journey: {names[tid]}")
        elif level == "high":
            score += WEIGHTS["high"]
            reasons.append(f"high journey: {names[tid]}")
        elif level:
            score += WEIGHTS["journey"]
            reasons.append(f"journey: {names[tid]}")
        history = outcomes.get(tid, [])
        if not history:
            score += WEIGHTS["never"]
            reasons.append(f"never run on {env}")
        else:
            if history[0] in FAILED:
                score += WEIGHTS["failing"]
                reasons.append("failed in its last run")
            flips = sum(1 for a, b in zip(history, history[1:]) if (a in FAILED) != (b in FAILED))
            if flips >= 2:
                score += WEIGHTS["flaky"]
                reasons.append("flaky lately")
            days = _days_since(last_at.get(tid, ""), now)
            if days is not None and days >= STALE_DAYS:
                score += WEIGHTS["stale"]
                reasons.append(f"not run for {int(days)} days")
        seconds = statistics.median(durations[tid]) if durations.get(tid) else typical
        items.append(Planned(tid, score, round(seconds, 1), reasons, must=level == "critical"))

    must = [p for p in items if p.must]
    rest = sorted((p for p in items if not p.must), key=lambda p: (-p.score / max(p.seconds, 1.0), -p.score,
                                                                   p.test_id))
    result.chosen = sorted(must, key=lambda p: (-p.score, p.test_id))
    spent = result.seconds
    if spent > budget_seconds:
        result.notes.append(f"The critical journeys' test cases alone take about {_fmt(spent)}, more than the "
                            f"budget of {_fmt(budget_seconds)}: they are all in anyway.")
    for p in rest:
        if p.score > 0 and spent + p.seconds <= budget_seconds:
            result.chosen.append(p)
            spent += p.seconds
        else:
            p.reasons = p.reasons + (["over the budget"] if p.score > 0 else ["nothing points to a risk"])
            result.left_out.append(p)
    risky_out = [p for p in result.left_out if p.score >= WEIGHTS["failing"]]
    if risky_out:
        result.notes.append(f"{len(risky_out)} risky test case{'' if len(risky_out) == 1 else 's'} did not fit "
                            "the budget: " + ", ".join(p.test_id for p in risky_out[:5])
                            + ("..." if len(risky_out) > 5 else ""))
    if not result.chosen:
        result.notes.append("Nothing points to a risk: no change, no failures, nothing critical or stale.")
    return result


def _fmt(seconds: float) -> str:
    return f"{seconds / 60:.1f} min" if seconds >= 90 else f"{int(seconds)} s"


def plan_for(svc, system: str, env: str, budget_seconds: float, *, base: str = "", head: str = "") -> Plan:
    """A plan from the app's own workspace, runs and journeys; with `base` (a git ref), for the change since it."""
    from webapp.services.workspace_model import model_for
    snap = model_for(svc.settings.workspace, svc.tests).snapshot()
    tests = sorted(tid for (s, tid), node in snap.tests.items() if s == system and node.steps)
    runs = svc.runs.query(system, env, finished_only=True, limit=30)
    impacted: dict[str, bool] = {}
    notes = []
    if base:
        report = svc.impact.report(base, head)
        impacted = {t.test_id: t.breaks for t in report.tests if t.system == system and t.runnable}
        notes.append(f"Change since {base}{' to ' + head if head else ''}: {len(report.changes)} change"
                     f"{'' if len(report.changes) == 1 else 's'}, reaching {len(impacted)} test case"
                     f"{'' if len(impacted) == 1 else 's'} of {system}.")
    result = plan(system, env, budget_seconds, tests, runs, journeys=svc.journeys.store.list(system),
                  impacted=impacted)
    result.notes = notes + result.notes
    return result
