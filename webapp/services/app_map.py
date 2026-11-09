"""Application map: the screens of a system and how tests move between them, with how well each one is tested.

The map is built from what the workspace and the runs already hold:
- screens: the system's pages (shared components are parts of pages, not screens);
- moves: two screens one after the other in a test case's steps, with common actions and reused test cases
  followed into, counted over all test cases (how the tests travel through the application);
- coverage: per screen, the test cases and journeys that go through it, and how those test cases did in the latest
  run of each environment.

Each screen gets a state for the heat map: untested (no test case reaches it), failing (a test case through it
failed in its environment's latest run), new (its page file is recent and few tests use it), or tested.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

NEW_DAYS = 14
MAX_DEPTH = 3
MAX_NODES = 80


@dataclass
class Screen:
    name: str
    file: str = ""
    tests: list[str] = field(default_factory=list)
    journeys: list[str] = field(default_factory=list)
    critical: bool = False  # a critical journey goes through it
    entry: int = 0  # test cases that start here
    results: dict[str, dict[str, int]] = field(default_factory=dict)  # env -> {passed, failed}
    state: str = "tested"  # untested | failing | new | tested
    new: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Move:
    source: str
    target: str
    tests: int


@dataclass
class AppMap:
    system: str
    screens: list[Screen]
    moves: list[Move]
    envs: list[str]

    def summary(self) -> dict:
        count = defaultdict(int)
        for s in self.screens:
            count[s.state] += 1
        return {"screens": len(self.screens), "moves": len(self.moves), **{k: count[k] for k in
                                                                            ("untested", "failing", "new", "tested")}}

    def to_dict(self) -> dict:
        return {"system": self.system, "envs": self.envs, "summary": self.summary(),
                "screens": [s.to_dict() for s in self.screens], "moves": [asdict(m) for m in self.moves]}


def sequence(steps: list[dict], common: dict[str, list[dict]], tests: dict[str, list[dict]],
             depth: int = 0) -> list[str]:
    """The screens a test case goes through, in order, following common actions and reused test cases."""
    out: list[str] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("commonAction"), str) and depth < MAX_DEPTH:
            out += sequence(common.get(step["commonAction"], []), common, tests, depth + 1)
        elif isinstance(step.get("testCase"), str) and depth < MAX_DEPTH:
            out += sequence(tests.get(step["testCase"], []), common, tests, depth + 1)
        screen = step.get("screen")
        if isinstance(screen, str) and screen:
            out.append(screen)
    return out


def build(system: str, pages: dict[str, str], tests: dict[str, list[dict]], common: dict[str, list[dict]],
          journeys: list[dict], latest: dict[str, dict[str, str]], *, components: set[str] | None = None,
          page_age_days: dict[str, float] | None = None) -> AppMap:
    """`pages`: screen -> page file; `tests`: test id -> steps; `latest`: env -> {test id: outcome} of the
    environment's latest finished run, an outcome being "passed"/"failed" or ("failed", screen it failed on);
    `page_age_days`: screen -> days since its file was created or changed.

    A failed test case counts against the screen it failed on when that is known, and against every screen it
    goes through when it is not."""
    components = components or set()
    page_age_days = page_age_days or {}
    screens = {name: Screen(name, rel) for name, rel in pages.items() if name not in components}
    moves: dict[tuple[str, str], set[str]] = defaultdict(set)
    through: dict[str, set[str]] = defaultdict(set)
    seqs: dict[str, list[str]] = {}
    for tid, steps in tests.items():
        seq = seqs[tid] = [s for s in sequence(steps, common, tests) if s not in components]
        if not seq:
            continue
        if seq[0] in screens:
            screens[seq[0]].entry += 1
        for s in seq:
            through[s].add(tid)
            screens.setdefault(s, Screen(s))  # a step naming a page that does not exist still shows on the map
        for a, b in zip(seq, seq[1:]):
            if a != b:
                moves[(a, b)].add(tid)
    by_test_journeys: dict[str, list[dict]] = defaultdict(list)
    for j in journeys:
        if str(j.get("system") or "") == system:
            for t in j.get("tests") or []:
                by_test_journeys[str(t)].append(j)
    for name, screen in screens.items():
        screen.tests = sorted(through.get(name, set()))
        js = {str(j.get("name")): j for t in screen.tests for j in by_test_journeys.get(t, [])}
        screen.journeys = sorted(js)
        screen.critical = any(str(j.get("criticality")) == "critical" for j in js.values())
        for env, outcomes in latest.items():
            counts = {"passed": 0, "failed": 0}
            for t in screen.tests:
                o = outcomes.get(t)
                where = None
                if isinstance(o, tuple):
                    o, where = o
                if o == "passed":
                    counts["passed"] += 1
                elif o in ("failed", "error"):
                    seq = seqs.get(t, [])
                    if not where or where not in seq or where == name:
                        counts["failed"] += 1
                    elif name in seq[:seq.index(where)]:
                        counts["passed"] += 1  # the test got through this screen and failed further on
            if counts["passed"] or counts["failed"]:
                screen.results[env] = counts
        age = page_age_days.get(name)
        screen.new = age is not None and age <= NEW_DAYS and len(screen.tests) <= 1
        if not screen.tests:
            screen.state = "untested"
        elif any(c["failed"] for c in screen.results.values()):
            screen.state = "failing"
        elif screen.new:
            screen.state = "new"
    ordered = sorted(screens.values(), key=lambda s: ({"failing": 0, "untested": 1, "new": 2}.get(s.state, 3),
                                                      -len(s.tests), s.name.casefold()))
    return AppMap(system, ordered, [Move(a, b, len(t)) for (a, b), t in sorted(moves.items(), key=lambda kv: (
        -len(kv[1]), kv[0]))], sorted(latest))


_ID = re.compile(r"[^A-Za-z0-9_]")


def mermaid(m: AppMap, limit: int = MAX_NODES) -> str:
    """The map as a Mermaid flowchart (left to right), colored by state; the busiest `limit` screens and every
    untested or failing one when there are more."""
    keep = [s for s in m.screens if s.state in ("failing", "untested")]
    keep += [s for s in m.screens if s not in keep]
    keep = keep[:limit]
    names = {s.name for s in keep}
    ids = {s.name: f"s{i}_{_ID.sub('_', s.name)[:30]}" for i, s in enumerate(keep)}
    lines = ["flowchart LR",
             "classDef untested fill:#fef3c7,stroke:#b45309,color:#1f2937",
             "classDef failing fill:#fee2e2,stroke:#b91c1c,color:#1f2937",
             "classDef new fill:#e0f2fe,stroke:#0369a1,color:#1f2937,stroke-dasharray:4 3",
             "classDef tested fill:#dcfce7,stroke:#15803d,color:#1f2937"]
    for s in keep:
        label = s.name.replace('"', "'")
        detail = "no test case" if not s.tests else f"{len(s.tests)} test case{'' if len(s.tests) == 1 else 's'}"
        lines.append(f'{ids[s.name]}["{label}<br/><small>{detail}</small>"]:::{s.state}')
    for mv in m.moves:
        if mv.source in names and mv.target in names:
            arrow = f"-->|{mv.tests}|" if mv.tests > 1 else "-->"
            lines.append(f"{ids[mv.source]} {arrow} {ids[mv.target]}")
    return "\n".join(lines)


def map_for(svc, system: str) -> AppMap:
    """The map of a system from the app's workspace model, pages, journeys and latest runs."""
    from webapp.services.workspace_model import model_for
    snap = model_for(svc.settings.workspace, svc.tests).snapshot()
    tests = {tid: node.steps for (s, tid), node in snap.tests.items() if s == system}
    refs = svc.pom.list_pages(system)
    pages = {r.name: r.rel for r in refs}
    components = {r.name for r in refs if r.component}
    base = svc.pom.pages_dir(system)
    now = time.time()
    ages = {}
    for r in refs:
        try:
            ages[r.name] = (now - Path(base, r.rel).stat().st_mtime) / 86400
        except OSError:
            pass
    latest: dict[str, dict[str, str]] = {}
    for env in svc.systems.environments(system):
        runs = svc.runs.query(system, env, finished_only=True, limit=1)
        if runs:
            latest[env] = {r.test_id: (r.outcome, _failed_screen(r)) if r.outcome in ("failed", "error")
                           else r.outcome for r in runs[0].results.values()}
    return build(system, pages, tests, snap.common, snap.journeys, latest, components=components,
                 page_age_days=ages)


def _failed_screen(result) -> str | None:
    """The screen a failed result failed on, from the step the runner recorded (None when it was not a page step)."""
    step = next((x for x in result.steps if x.get("status") == "failed"), None)
    return str(step.get("label")) if step and step.get("kind") == "page" and step.get("label") else None
