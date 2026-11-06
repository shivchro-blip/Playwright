"""Longer-term view of the run history: pass rate by day, the slowest tests, and where failures happen.

Works on the runs the RunManager already has (run.json of each run); the failing screen and field come from the
Allure results through RunArtifacts, for the most recent runs only so the dialog opens fast.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Iterable

from webapp.services.run_insights import FAILING, FINAL, PASSING, finished
from webapp.services.run_manager import Run


@dataclass
class DayPoint:
    day: str  # YYYY-MM-DD
    runs: int = 0
    passed: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def rate(self) -> float | None:
        return round(100 * self.passed / self.total, 1) if self.total else None


@dataclass
class SlowTest:
    test_id: str
    browser: str
    average: float
    slowest: float
    latest: float
    runs: int


@dataclass
class FailureSpot:
    screen: str
    field_name: str
    action: str
    failures: int = 0
    tests: set[str] = field(default_factory=set)
    runs: set[str] = field(default_factory=set)
    last_run: str = ""

    @property
    def where(self) -> str:
        return f"{self.screen} > {self.field_name} ({self.action})" if self.field_name else (self.screen or "unknown")


def _day(run: Run) -> str:
    return (run.started_at or run.created_at)[:10]


def _scope(runs: Iterable[Run], system: str | None, env: str | None) -> list[Run]:
    return finished(runs, system or None, env or None)


def daily(runs: Iterable[Run], days: int = 30, system: str | None = None, env: str | None = None,
          today: date | None = None) -> list[DayPoint]:
    """One point per day for the last `days` days (days with no run have no results), oldest first."""
    today = today or datetime.now().date()
    points = {(today - timedelta(days=i)).isoformat(): None for i in range(days - 1, -1, -1)}
    points = {d: DayPoint(d) for d in points}
    for run in _scope(runs, system, env):
        point = points.get(_day(run))
        if point is None:
            continue
        point.runs += 1
        for r in list(run.results.values()):
            if r.outcome in PASSING | {"xpassed"}:
                point.passed += 1
            elif r.outcome in FAILING:
                point.failed += 1
    return list(points.values())


def slowest(runs: Iterable[Run], n: int = 10, system: str | None = None, env: str | None = None,
            last_runs: int = 20) -> list[SlowTest]:
    """Tests with the longest average duration over their results in the last `last_runs` finished runs."""
    times: dict[tuple[str, str], list[float]] = defaultdict(list)
    for run in _scope(runs, system, env)[-last_runs:]:
        for r in list(run.results.values()):
            if r.outcome in FINAL and r.outcome != "skipped" and r.duration > 0:
                times[(r.test_id, r.browser)].append(r.duration)
    rows = [SlowTest(t, b, round(statistics.fmean(v), 1), round(max(v), 1), round(v[-1], 1), len(v))
            for (t, b), v in times.items()]
    return sorted(rows, key=lambda r: (-r.average, r.test_id))[:n]


def failure_spots(runs: Iterable[Run], details_for: Callable[[Run], dict], last_runs: int = 15,
                  system: str | None = None, env: str | None = None, n: int = 10) -> list[FailureSpot]:
    """Where failures happen (screen, field, action), most frequent first, over the last `last_runs` runs."""
    spots: dict[tuple[str, str, str], FailureSpot] = {}
    for run in _scope(runs, system, env)[-last_runs:]:
        details = details_for(run)
        for r in list(run.results.values()):
            if r.outcome not in FAILING:
                continue
            item = details.get((r.test_id, r.browser))
            point = item.failure if item else None
            key = (point.screen, point.field_name, point.action) if point else ("(no step detail)", "", "")
            spot = spots.setdefault(key, FailureSpot(*key))
            spot.failures += 1
            spot.tests.add(r.test_id)
            spot.runs.add(run.id)
            spot.last_run = run.id
    return sorted(spots.values(), key=lambda s: (-s.failures, s.where))[:n]


def summary(points: list[DayPoint]) -> dict:
    total = sum(p.total for p in points)
    passed = sum(p.passed for p in points)
    return {"runs": sum(p.runs for p in points), "results": total,
            "rate": round(100 * passed / total, 1) if total else None}
