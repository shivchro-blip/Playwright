"""Journeys: the business transactions a system exists to serve, and whether each one works right now.

A journey ("Business registration", "Annual report filing") is a named group of the test cases that prove it, with a
criticality and an owner. It lives in <workspace>/Data/journeys.json:

    { "journeys": [ { "id": "business-registration", "name": "Business registration", "system": "PORTAL",
                      "criticality": "critical", "owner": "Licensing team", "description": "...",
                      "tests": ["TC-REG-001", "TC-REG-002"] } ] }

Its status on an environment comes from the runs the app already has: the latest result of each of its test cases.
Its coverage comes from the steps of those test cases (and the common actions they call) against the pages they use.
Test cases, pages and runs are not changed by any of this.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from utils.steps_v2 import form_data
from webapp.services.json_files import NAME_RE, ConflictError, read_json, write_json
from webapp.services.run_insights import FAILING, FINISHED_RUNS, PASSING
from webapp.services.run_manager import query_runs

CRITICALITY = ("critical", "high", "normal")
STATES = ("failing", "incomplete", "stale", "not_run", "passing")  # worst first
STATE_LABELS = {"failing": "failing", "incomplete": "partly run", "stale": "not run recently",
                "not_run": "never run", "passing": "passing"}
STALE_DAYS = 7
WINDOW = 10
SET_SUFFIX = re.compile(r"\[[^\]]*\]$")


class JourneyError(ValueError):
    """Shown to the user as is."""


@dataclass
class Journey:
    id: str
    name: str
    system: str
    criticality: str = "normal"
    owner: str = ""
    description: str = ""
    tests: list[str] = field(default_factory=list)
    monitor_safe: bool = False  # its tests may run against production (they create no real records)

    def to_dict(self) -> dict:
        out = {"id": self.id, "name": self.name, "system": self.system, "criticality": self.criticality,
               "owner": self.owner, "description": self.description, "tests": list(self.tests)}
        if self.monitor_safe:
            out["monitor_safe"] = True
        return out


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "journey"


def validate(data: dict) -> Journey:
    if not isinstance(data, dict):
        raise JourneyError("A journey is an object.")
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 80:
        raise JourneyError("Give the journey a name (at most 80 characters).")
    system = str(data.get("system") or "").strip()
    if not system or not NAME_RE.match(system) or system in (".", ".."):
        raise JourneyError("Pick the system the journey belongs to.")
    criticality = str(data.get("criticality") or "normal").strip().lower()
    if criticality not in CRITICALITY:
        raise JourneyError(f"Criticality must be one of {', '.join(CRITICALITY)}.")
    tests = [str(t).strip() for t in data.get("tests") or [] if str(t).strip()]
    jid = str(data.get("id") or "").strip() or slug(name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", jid):
        raise JourneyError("Invalid journey id.")
    return Journey(id=jid, name=name, system=system, criticality=criticality,
                   owner=str(data.get("owner") or "").strip()[:80],
                   description=str(data.get("description") or "").strip()[:500],
                   tests=list(dict.fromkeys(tests)), monitor_safe=bool(data.get("monitor_safe", False)))


class JourneyStore:
    """Data/journeys.json; every write is checked against the version read and kept in the edit history."""

    def __init__(self, workspace: Path):
        self.path = Path(workspace) / "Data" / "journeys.json"

    def _read(self) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            return [], None
        data, sha, _ = read_json(self.path)
        items = data.get("journeys") if isinstance(data, dict) else None
        return (items if isinstance(items, list) else []), sha

    def list(self, system: str | None = None) -> list[Journey]:
        try:
            items, _ = self._read()
        except (OSError, ValueError):
            return []
        out = []
        for item in items:
            try:
                journey = validate(item)
            except JourneyError:
                continue
            if system is None or journey.system == system:
                out.append(journey)
        rank = {c: i for i, c in enumerate(CRITICALITY)}
        return sorted(out, key=lambda j: (rank[j.criticality], j.name.casefold()))

    def get(self, jid: str) -> Journey | None:
        return next((j for j in self.list() if j.id == jid), None)

    def save(self, journey: Journey, replaces: str | None = None) -> Journey:
        """Add a journey, or replace the one with id `replaces` (its id may change with the name)."""
        items, sha = self._read()
        ids = lambda rows: {i.get("id") for i in rows if isinstance(i, dict)}  # noqa: E731
        others = [i for i in items if not (replaces and isinstance(i, dict) and i.get("id") == replaces)]
        if replaces is None:  # a new journey never replaces another: give it a free id
            base, n = journey.id, 2
            while journey.id in ids(others):
                journey.id, n = f"{base}-{n}", n + 1
        elif journey.id in ids(others):
            raise JourneyError(f"Another journey already has the id {journey.id!r}; choose another name.")
        others.append(journey.to_dict())
        others.sort(key=lambda i: str(i.get("name", "")).casefold())
        try:
            write_json(self.path, {"journeys": others}, sha, indent="  ")
        except ConflictError as e:
            raise JourneyError(f"{e} Reopen the Journeys tab and try again.") from e
        return journey

    def delete(self, jid: str) -> bool:
        items, sha = self._read()
        kept = [i for i in items if not (isinstance(i, dict) and i.get("id") == jid)]
        if len(kept) == len(items):
            return False
        write_json(self.path, {"journeys": kept}, sha, indent="  ")
        return True


# ───────────── status ─────────────

@dataclass
class TestState:
    test_id: str
    outcome: str = ""  # latest final outcome on this environment, "" = never run
    run_id: str = ""
    at: str = ""
    browser: str = ""
    where: str = ""  # failing screen > field (action), when it failed
    retries: int = 0  # times it was run again before this result (passed with retries = flaky)
    healed: int = 0  # locators healed during that run
    message: str = ""


@dataclass
class JourneyStatus:
    env: str
    state: str
    tests: list[TestState] = field(default_factory=list)
    last_run: str = ""
    last_at: str = ""
    pass_rate: float | None = None  # % of recent runs of this journey where every test that ran passed
    runs_counted: int = 0
    where: str = ""  # where the first failing test failed

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]


def _base(test_id: str) -> str:
    return SET_SUFFIX.sub("", test_id or "")


def status(journey: Journey, runs: Iterable, env: str, *, now: datetime | None = None,
           details_for: Callable | None = None, stale_days: int = STALE_DAYS, window: int = WINDOW,
           since: str | None = None) -> JourneyStatus:
    """The journey on one environment, from the finished runs of its system there (newest wins).
    `since` (ISO time): only runs started from then on count, e.g. since the release was deployed."""
    now = now or datetime.now()
    wanted = set(journey.tests)
    mine = sorted((r for r in runs if r.status in FINISHED_RUNS and r.config.system == journey.system
                   and r.config.env == env and (not since or r.created_at >= since)), key=lambda r: r.created_at)
    latest: dict[str, TestState] = {t: TestState(t) for t in journey.tests}
    verdicts: list[bool] = []
    for run in mine:
        seen: list[bool] = []
        for r in list(run.results.values()):
            base = _base(r.test_id)
            if base not in wanted or r.outcome not in PASSING | FAILING:
                continue
            seen.append(r.outcome in PASSING)
            state = latest[base]
            # one result per run and test: a failing browser or data set makes the test failing in that run
            if state.run_id != run.id or r.outcome in FAILING:
                state.outcome, state.run_id, state.browser = r.outcome, run.id, r.browser
                state.at = run.finished_at or run.started_at or run.created_at
                state.where, state.retries, state.message = "", r.retries, (r.message or "")[:300]
                state.healed = r.healed
                if r.outcome in FAILING and details_for:
                    item = details_for(run).get((r.test_id, r.browser))
                    state.where = item.failure.short if item and item.failure else ""
        if seen:
            verdicts.append(all(seen))
    tests = list(latest.values())
    ran = [t for t in tests if t.outcome]
    result = JourneyStatus(env=env, state="not_run", tests=tests)
    if ran:
        newest = max(ran, key=lambda t: t.at)
        result.last_run, result.last_at = newest.run_id, newest.at
    recent = verdicts[-window:]
    if recent:
        result.pass_rate, result.runs_counted = round(100 * sum(recent) / len(recent), 1), len(recent)
    if not journey.tests or not ran:
        return result
    failing = [t for t in tests if t.outcome in FAILING]
    if failing:
        result.state, result.where = "failing", next((t.where for t in failing if t.where), "")
    elif len(ran) < len(tests):
        result.state = "incomplete"
    elif _age_days(min(t.at for t in ran), now) > stale_days:
        result.state = "stale"
    else:
        result.state = "passing"
    return result


def _age_days(stamp: str, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(stamp[:19])).total_seconds() / 86400
    except ValueError:
        return float("inf")


# ───────────── coverage ─────────────

@dataclass
class ScreenCoverage:
    screen: str
    used: list[str]  # fields the journey's steps touch
    total: int  # fields the page has (0 = page not found)

    @property
    def percent(self) -> float | None:
        return round(100 * len(self.used) / self.total, 1) if self.total else None


@dataclass
class Coverage:
    screens: list[ScreenCoverage] = field(default_factory=list)
    missing_tests: list[str] = field(default_factory=list)  # IDs not found in the system's test files
    steps: int = 0

    @property
    def percent(self) -> float | None:
        total = sum(s.total for s in self.screens)
        used = sum(len(s.used) for s in self.screens if s.total)
        return round(100 * used / total, 1) if total else None


def coverage(journey: Journey, steps_of: Callable[[str], list[dict] | None],
             fields_of: Callable[[str], set[str] | None], actions: Callable[[str], list[dict]]) -> Coverage:
    """steps_of(test id) -> its steps (None: not found); fields_of(page) -> its field names (None: no page);
    actions(name) -> the steps of a common action."""
    from webapp.services.test_case_service import base_field

    used: dict[str, set[str]] = {}
    result = Coverage()

    def walk(steps: list[dict], depth: int = 0) -> None:
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            result.steps += 1
            if isinstance(step.get("commonAction"), str) and depth < 3:
                walk(actions(step["commonAction"]), depth + 1)
            elif isinstance(step.get("testCase"), str) and depth < 3:
                walk(steps_of(step["testCase"]) or [], depth + 1)
            screen = step.get("screen")
            if isinstance(screen, str) and screen:
                bucket = used.setdefault(screen, set())
                for key in form_data(step):  # classic formData or a v2 "do" list
                    bucket.add(base_field(str(key)))
            override = step.get("override")
            if isinstance(override, str) and override.count("|") >= 2:
                page, name, _ = override.split("|", 2)
                used.setdefault(page, set()).add(name)

    for test_id in journey.tests:
        steps = steps_of(test_id)
        if steps is None:
            result.missing_tests.append(test_id)
            continue
        walk(steps)
    for screen in sorted(used, key=str.casefold):
        names = fields_of(screen)
        known = sorted(used[screen] & names, key=str.casefold) if names is not None else sorted(used[screen])
        result.screens.append(ScreenCoverage(screen, known, len(names) if names is not None else 0))
    return result


# ───────────── for the app ─────────────

@dataclass
class Overview:
    journey: Journey
    statuses: dict[str, JourneyStatus]  # env -> status
    coverage: Coverage

    @property
    def worst(self) -> str:
        states = [s.state for s in self.statuses.values()]
        return min(states, key=STATES.index) if states else "not_run"


class JourneyService:
    def __init__(self, store: JourneyStore, tests, common, catalog, systems, runs, details_for: Callable):
        self.store, self.tests, self.common, self.catalog = store, tests, common, catalog
        self.systems, self.runs, self.details_for = systems, runs, details_for

    def _model(self):
        from webapp.services.workspace_model import model_for
        return model_for(self.tests.workspace, self.tests).snapshot()

    def _steps_index(self, system: str) -> dict[str, list[dict]]:
        return dict(self._model().by_folder.get(system, {}))

    def _fields_index(self, system: str) -> dict[str, set[str]]:
        return {page: set(fields) for (s, page), fields in self._model().pages.items() if s == system}

    def _action_steps(self, name: str) -> list[dict]:
        return list(self._model().common.get(name, []))

    def _steps_index_files(self, system: str) -> dict[str, list[dict]]:
        from webapp.services.usage import list_sources

        out: dict[str, list[dict]] = {}
        for source in list_sources(self.tests):
            if source.kind != "test" or source.folder != system:
                continue
            try:
                data, _, _ = read_json(source.path)
            except (OSError, ValueError):
                continue
            for case in (data.get("testCases") or []) if isinstance(data, dict) else []:
                if isinstance(case, dict) and case.get("testCaseID"):
                    out[str(case["testCaseID"])] = list(case.get("steps") or [])
        return out

    def _fields_index_files(self, system: str) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for ref in self.tests.pom.list_pages(system):
            try:
                data, _ = self.tests.pom.load(system, ref.rel)
            except (OSError, ValueError):
                continue
            out[Path(ref.rel).stem] = set((data.get("fields") or {}).keys())
        return out

    def _action_steps_files(self, name: str) -> list[dict]:
        try:
            return list(self.common.load(name)[0].get("steps") or [])
        except (OSError, ValueError):
            return []

    def overview(self, system: str, envs: list[str] | None = None) -> list[Overview]:
        envs = envs if envs is not None else self.systems.environments(system)
        runs = query_runs(self.runs, system, finished_only=True)
        steps, fields = self._steps_index(system), self._fields_index(system)
        cache: dict[str, dict] = {}

        def details(run):
            if run.id not in cache:
                cache[run.id] = self.details_for(run)
            return cache[run.id]

        out = []
        for journey in self.store.list(system):
            cov = coverage(journey, steps.get, fields.get, self._action_steps)
            out.append(Overview(journey, {env: status(journey, runs, env, details_for=details) for env in envs},
                                cov))
        return out

    def evaluate(self, system: str, env: str, journey_ids: list[str] | None = None, since: str | None = None,
                 stale_days: int = STALE_DAYS) -> list[tuple[Journey, JourneyStatus, Coverage]]:
        """Each chosen journey (all of the system's when None) on one environment, for a release check."""
        runs = query_runs(self.runs, system, env, since=since, finished_only=True)
        steps, fields = self._steps_index(system), self._fields_index(system)
        cache: dict[str, dict] = {}

        def details(run):
            if run.id not in cache:
                cache[run.id] = self.details_for(run)
            return cache[run.id]

        chosen = [j for j in self.store.list(system) if journey_ids is None or j.id in journey_ids]
        return [(j, status(j, runs, env, details_for=details, since=since, stale_days=stale_days),
                 coverage(j, steps.get, fields.get, self._action_steps)) for j in chosen]

    def unassigned(self, system: str) -> list[str]:
        taken = {t for j in self.store.list(system) for t in j.tests}
        return sorted((t.test_id for t in self.catalog.load(system).tests if t.test_id not in taken),
                      key=str.casefold)

    def test_ids(self, system: str) -> list[str]:
        return sorted({t.test_id for t in self.catalog.load(system).tests}, key=str.casefold)

    def run_selection(self, journey: Journey) -> tuple[list[str], list[str]]:
        """(tags, test IDs) that run exactly the journey's test cases. Raises JourneyError if none can run."""
        return self.selection(journey.system, journey.tests, "this journey's")

    def selection(self, system: str, test_ids: list[str], whose: str = "the") -> tuple[list[str], list[str]]:
        """(tags, test IDs) that run exactly these test cases of a system. Raises JourneyError if none can run."""
        known = {t.test_id: t for t in self.catalog.load(system).tests}
        runnable = [t for t in test_ids if t in known and known[t].steps]
        if not runnable:
            raise JourneyError(f"None of {whose} test cases can run (missing or without steps).")
        tags = sorted({tag for t in runnable for tag in known[t].tags}, key=str.casefold)
        if not tags:
            raise JourneyError(f"{whose.capitalize()} test cases have no tags, and the framework only runs "
                               "tagged tests.")
        return tags, runnable
