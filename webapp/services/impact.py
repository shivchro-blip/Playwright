"""Change impact: which test cases and journeys a change reaches, so a release tests what it touched.

A change is something in the workspace (a page's field locator, a test case, a common action, a data file, the
system's settings) or a screen of the application that its developers say they changed. From git (the files that
differ between two commits, compared key by key) or from a list of screens, the impact is worked out on the
workspace as it is now:

- a test case is impacted when it changed, when it calls a common action or reuses a test case that changed, when
  it uses a changed page or field, when its data file changed, or when its system's settings changed;
- calls are followed through common actions and reused test cases (three levels, like the runner);
- a changed field that no test case uses is "not covered": a change nothing will check;
- a test case that uses a field or page that was removed will break, and is flagged first.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from webapp.services.test_case_service import base_field

SUITE = "MasterRegressionSuite"
DEPTH = 3
# workspace files that hold the app's own state, not how tests behave
IGNORED = {"Data/journeys.json", "Data/monitors.json", "Data/quarantine.json", "Data/heals.json",
           "Data/schedules.json", "Data/maskPatterns.json"}
IGNORED_PREFIXES = ("Data/locatorProfiles/", ".history/", "reports/")
REF_RE = re.compile(r"^[\w./~^@{}-]{1,200}$")


class ImpactError(ValueError):
    """Shown to the user as is."""


@dataclass(frozen=True)
class Change:
    kind: str  # page | field | test | common | data | system | other
    name: str  # page, test case ID, common action, data file, system, or file path
    system: str = ""  # for page, field, test and system changes
    field: str = ""  # for field changes
    how: str = "changed"  # added | removed | changed | app (the application's screen changed)

    @property
    def label(self) -> str:
        where = {"page": f"page {self.name}", "field": f"{self.name} > {self.field}", "test": f"test {self.name}",
                 "common": f"common action {self.name}", "data": f"data file {self.name}",
                 "system": f"settings of {self.name}", "other": self.name}[self.kind]
        return f"{where} ({'screen changed in the app' if self.how == 'app' else self.how})"


# ───────────── what changed: key by key ─────────────

def _dict(data, key: str) -> dict:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def diff_page(system: str, page: str, old: dict | None, new: dict | None) -> list[Change]:
    if old is None or new is None:
        return [Change("page", page, system, how="added" if old is None else "removed")]
    out: list[Change] = []
    before, after = _dict(old, "fields"), _dict(new, "fields")
    for name in sorted(set(before) | set(after), key=str.casefold):
        if name not in before:
            out.append(Change("field", page, system, name, "added"))
        elif name not in after:
            out.append(Change("field", page, system, name, "removed"))
        elif before[name] != after[name]:
            out.append(Change("field", page, system, name))
    rest = lambda d: {k: v for k, v in (d or {}).items() if k != "fields"}  # noqa: E731
    if rest(old) != rest(new):  # the page's own settings (frame, url...) touch every field
        out.append(Change("page", page, system))
    return out


def _cases(data) -> dict[str, dict]:
    cases = data.get("testCases") if isinstance(data, dict) else None
    return {str(c["testCaseID"]): c for c in cases or [] if isinstance(c, dict) and c.get("testCaseID")}


def diff_tests(system: str, old: dict | None, new: dict | None) -> list[Change]:
    before, after = _cases(old), _cases(new)
    out = []
    for tid in sorted(set(before) | set(after), key=str.casefold):
        if tid not in before:
            out.append(Change("test", tid, (after[tid].get("system") or system), how="added"))
        elif tid not in after:
            out.append(Change("test", tid, (before[tid].get("system") or system), how="removed"))
        elif before[tid] != after[tid]:
            out.append(Change("test", tid, (after[tid].get("system") or system)))
    return out


def diff_common(old: dict | None, new: dict | None) -> list[Change]:
    before, after = _dict(old, "commonActions"), _dict(new, "commonActions")
    return [Change("common", n, how="added" if n not in before else "removed" if n not in after else "changed")
            for n in sorted(set(before) | set(after), key=str.casefold) if before.get(n) != after.get(n)]


def diff_systems(old: dict | None, new: dict | None) -> list[Change]:
    def systems(data) -> dict:
        return {k: v for k, v in (data or {}).items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    before, after = systems(old), systems(new)
    return [Change("system", s, s) for s in sorted(set(before) | set(after), key=str.casefold)
            if before.get(s) != after.get(s)]


def diff_file(rel: str, old: dict | None, new: dict | None) -> list[Change]:
    """Changes in one workspace file (path relative to the workspace, with /)."""
    if rel in IGNORED or rel.startswith(IGNORED_PREFIXES) or not rel.endswith(".json"):
        return []
    parts = PurePosixPath(rel).parts
    if parts[0] == "Pages" and len(parts) >= 3:
        return diff_page(parts[1], PurePosixPath(rel).stem, old, new)
    if parts[0] == "Tests" and len(parts) >= 3 and parts[1] == "com":
        return diff_common(old, new)
    if parts[0] == "Tests" and len(parts) >= 4 and parts[2] == SUITE:
        return diff_tests(parts[1], old, new)
    if parts[:2] == ("Data", "TestData") and len(parts) == 3:
        return [Change("data", PurePosixPath(rel).stem, how="added" if old is None else
                       "removed" if new is None else "changed")] if old != new else []
    if rel == "Data/systemData.json":
        return diff_systems(old, new)
    return [Change("other", rel)] if old != new else []


# ───────────── what changed: from git ─────────────

def _git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ImpactError(f"git could not run: {e}") from e
    if done.returncode != 0:
        raise ImpactError((done.stderr or done.stdout).strip().splitlines()[-1] if (done.stderr or done.stdout)
                          else f"git {args[0]} failed")
    return done.stdout


def _parse(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        data = json.loads(text.lstrip("﻿"))
    except ValueError:
        return {"__unreadable__": text[:200]}
    return data if isinstance(data, dict) else {"__value__": data}


def changes_from_git(workspace: Path, base: str, head: str = "") -> list[Change]:
    """What changed in the workspace between two commits (head empty: the files as they are now, including
    changes not committed yet and new files)."""
    for ref in (base, head):
        if ref and (not REF_RE.match(ref) or ref.startswith("-")):
            raise ImpactError(f"Not a git commit or branch: {ref!r}")
    if not base:
        raise ImpactError("Say what to compare with: a commit, a tag or a branch (for example origin/main).")
    workspace = Path(workspace).resolve()
    top = Path(_git(workspace, "rev-parse", "--show-toplevel").strip()).resolve()
    inside = workspace.relative_to(top).as_posix() if workspace != top else ""
    prefix = f"{inside}/" if inside else ""
    _git(top, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    if head:
        _git(top, "rev-parse", "--verify", "--quiet", f"{head}^{{commit}}")
    names = _git(top, "diff", "--name-only", "--no-renames", base, *([head] if head else []), "--",
                 inside or ".").splitlines()
    if not head:  # files git does not know yet
        names += _git(top, "ls-files", "--others", "--exclude-standard", "--", inside or ".").splitlines()

    def show(ref: str, path: str) -> str | None:
        try:
            return _git(top, "show", f"{ref}:{path}")
        except ImpactError:
            return None

    out: list[Change] = []
    for name in sorted(set(n.strip() for n in names if n.strip())):
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        old = _parse(show(base, name))
        if head:
            new = _parse(show(head, name))
        else:
            path = top / name
            new = _parse(path.read_text(encoding="utf-8-sig")) if path.is_file() else None
        out += diff_file(rel, old, new)
    return list(dict.fromkeys(out))


# ───────────── who it reaches ─────────────

@dataclass
class TestNode:
    test_id: str
    system: str
    file: str
    tags: list[str]
    steps: list[dict]
    data_set: str = ""


@dataclass
class Model:
    """The workspace as it is now: test cases per system and common actions."""
    tests: dict[tuple[str, str], TestNode] = field(default_factory=dict)  # (system, id)
    common: dict[str, list[dict]] = field(default_factory=dict)


def load_model(workspace: Path) -> Model:
    workspace = Path(workspace)
    model = Model()

    def read(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None

    tests_root = workspace / "Tests"
    for suite in sorted(tests_root.glob(f"*/{SUITE}")) if tests_root.is_dir() else []:
        folder = suite.parent.name
        for path in sorted(suite.rglob("*.json")):
            for case in _cases(read(path)).values():
                system = str(case.get("system") or folder)
                tags = case.get("tags") or []
                model.tests[(system, str(case["testCaseID"]))] = TestNode(
                    str(case["testCaseID"]), system, path.relative_to(suite).as_posix(),
                    [str(t) for t in tags] if isinstance(tags, list) else [str(tags)],
                    [s for s in case.get("steps") or [] if isinstance(s, dict)], str(case.get("dataSet") or ""))
    for path in sorted((tests_root / "com").rglob("*.json")) if (tests_root / "com").is_dir() else []:
        for name, action in _dict(read(path), "commonActions").items():
            model.common[str(name)] = [s for s in (action or {}).get("steps") or [] if isinstance(s, dict)] \
                if isinstance(action, dict) else []
    return model


@dataclass
class Footprint:
    """Everything one test case touches, through the common actions and test cases it calls."""
    pages: set[str] = field(default_factory=set)
    fields: set[tuple[str, str]] = field(default_factory=set)
    common: set[str] = field(default_factory=set)
    tests: set[str] = field(default_factory=set)  # reused test cases


def footprint(node: TestNode, model: Model) -> Footprint:
    fp = Footprint()

    def walk(steps: list[dict], depth: int) -> None:
        for step in steps:
            if isinstance(step.get("commonAction"), str):
                name = step["commonAction"]
                if name not in fp.common:
                    fp.common.add(name)
                    if depth < DEPTH:
                        walk(model.common.get(name, []), depth + 1)
            if isinstance(step.get("testCase"), str):
                tid = step["testCase"]
                if tid not in fp.tests:
                    fp.tests.add(tid)
                    reused = model.tests.get((node.system, tid))
                    if reused and depth < DEPTH:
                        walk(reused.steps, depth + 1)
            screen = step.get("screen")
            if isinstance(screen, str) and screen:
                fp.pages.add(screen)
                form = step.get("formData")
                for key in form if isinstance(form, dict) else []:
                    fp.fields.add((screen, base_field(str(key))))
            override = step.get("override")
            if isinstance(override, str) and override.count("|") >= 2:
                page, name, _ = override.split("|", 2)
                fp.pages.add(page)
                fp.fields.add((page, name))

    walk(node.steps, 0)
    return fp


@dataclass
class ImpactedTest:
    test_id: str
    system: str
    file: str
    reasons: list[str]
    breaks: bool = False  # uses something that was removed
    runnable: bool = True
    journeys: list[str] = field(default_factory=list)


@dataclass
class ImpactedJourney:
    id: str
    name: str
    system: str
    criticality: str
    tests: list[str]
    of: int  # how many test cases the journey has


@dataclass
class Report:
    changes: list[Change]
    tests: list[ImpactedTest]
    journeys: list[ImpactedJourney]
    not_covered: list[Change]  # changes no test case exercises
    removed_tests: list[str]
    everything: list[str] = field(default_factory=list)  # systems where a change reaches every test

    def to_dict(self) -> dict:
        return {"changes": [{**asdict(c), "label": c.label} for c in self.changes],
                "tests": [asdict(t) for t in self.tests], "journeys": [asdict(j) for j in self.journeys],
                "not_covered": [{**asdict(c), "label": c.label} for c in self.not_covered],
                "removed_tests": self.removed_tests, "everything": self.everything,
                "selection": self.selection()}

    def selection(self) -> dict[str, list[str]]:
        """system -> the impacted test cases that can run, the breaking ones first."""
        out: dict[str, list[str]] = {}
        for t in self.tests:
            if t.runnable:
                out.setdefault(t.system, []).append(t.test_id)
        return out


CRIT_ORDER = {"critical": 0, "high": 1, "normal": 2}


def analyze(changes: list[Change], model: Model, journeys: list | None = None) -> Report:
    """`journeys`: Journey objects (any system) to say which of them the change reaches."""
    by_system: dict[str, list[Change]] = {}
    shared: list[Change] = []  # common actions, data files and other files belong to no system
    for c in changes:
        (by_system.setdefault(c.system, []) if c.system else shared).append(c)
    broad_all = any(c.kind == "other" for c in changes)
    everything = sorted({c.system for c in changes if c.kind == "system"}, key=str.casefold)
    if broad_all:
        everything = sorted({n.system for n in model.tests.values()}, key=str.casefold)
    impacted: list[ImpactedTest] = []
    reached: set[Change] = set()
    for (system, tid), node in sorted(model.tests.items(), key=lambda kv: (kv[0][0].casefold(), kv[0][1].casefold())):
        fp = footprint(node, model)
        reasons, breaks = [], False
        for c in by_system.get(system, []) + shared:
            hit = False
            if c.kind == "test":
                hit = c.name == tid or c.name in fp.tests
                if hit and c.name != tid:
                    reasons.append(f"reuses test {c.name}, which " + ("was removed" if c.how == "removed" else c.how))
                    breaks |= c.how == "removed"
                elif hit:
                    reasons.append("the test case itself " + ("is new" if c.how == "added" else "changed"))
            elif c.kind == "common":
                hit = c.name in fp.common
                if hit:
                    reasons.append(f"calls common action {c.name}, which " + ("was removed" if c.how == "removed"
                                                                              else c.how))
                    breaks |= c.how == "removed"
            elif c.kind == "page":
                hit = c.name in fp.pages
                if hit:
                    reasons.append(f"uses page {c.name}" + (", which was removed" if c.how == "removed" else
                                                            ", changed in the app" if c.how == "app" else
                                                            ", whose settings changed"))
                    breaks |= c.how == "removed"
            elif c.kind == "field":
                hit = (c.name, c.field) in fp.fields
                if hit:
                    reasons.append(f"uses {c.name} > {c.field}" + (", which was removed" if c.how == "removed"
                                                                   else f" ({c.how})"))
                    breaks |= c.how == "removed"
            elif c.kind == "data":
                hit = node.data_set == c.name
                if hit:
                    reasons.append(f"reads data file {c.name}" + (", which was removed" if c.how == "removed"
                                                                  else ""))
                    breaks |= c.how == "removed"
            if hit:
                reached.add(c)
        if system in everything and not reasons:
            reasons.append("its system's settings changed" if any(c.kind == "system" and c.name == system
                                                                   for c in changes)
                           else "a shared workspace file changed")
        if reasons:
            impacted.append(ImpactedTest(tid, system, node.file, reasons, breaks, bool(node.steps and node.tags)))
    for c in changes:
        if c.kind == "system" or (c.kind == "other" and impacted):
            reached.add(c)
    removed = sorted({c.name for c in changes if c.kind == "test" and c.how == "removed"}, key=str.casefold)
    not_covered = [c for c in changes if c not in reached and not (c.kind == "test" and c.how == "removed")]
    impacted.sort(key=lambda t: (not t.breaks, t.system.casefold(), t.test_id.casefold()))
    found: list[ImpactedJourney] = []
    hit_ids = {(t.system, t.test_id) for t in impacted}
    for j in journeys or []:
        mine = [t for t in j.tests if (j.system, t) in hit_ids]
        if mine:
            found.append(ImpactedJourney(j.id, j.name, j.system, j.criticality, mine, len(j.tests)))
            for t in impacted:
                if t.system == j.system and t.test_id in mine:
                    t.journeys.append(j.name)
    found.sort(key=lambda j: (CRIT_ORDER.get(j.criticality, 3), j.name.casefold()))
    return Report(list(changes), impacted, found, not_covered, removed, everything)


def app_changes(screens: dict[str, list[str]]) -> list[Change]:
    """Screens of the application its developers changed: system -> page names."""
    return [Change("page", page, system, how="app") for system, pages in (screens or {}).items()
            for page in pages if page]


class ImpactService:
    def __init__(self, workspace: Path, journeys, start_tests: Callable[[str, list[str], str, str], object]):
        self.workspace, self.journeys, self.start_tests = Path(workspace), journeys, start_tests

    def report(self, base: str = "", head: str = "", screens: dict[str, list[str]] | None = None) -> Report:
        changes: list[Change] = []
        if base:
            changes += changes_from_git(self.workspace, base, head)
        changes += app_changes(screens or {})
        if not base and not screens:
            raise ImpactError("Compare with a commit or branch, or pick the screens the application changed.")
        return analyze(list(dict.fromkeys(changes)), load_model(self.workspace), self.journeys.store.list())

    def run(self, report: Report, env: str, trigger: str = "impact") -> list:
        selection = report.selection()
        if not selection:
            raise ImpactError("No test case the change reaches can run.")
        return [self.start_tests(system, ids, env, trigger) for system, ids in selection.items()]
