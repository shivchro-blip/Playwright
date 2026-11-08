"""Failure intelligence: failures grouped by cause, across tests and runs, each group explained with its evidence.

A failed result's cause signature is where it failed (the screen and field, from the report's failing step) and what
kind of error it was (the message, with the parts that change from run to run taken out: numbers, quoted values,
addresses). Failures with the same signature are one group: "18 tests fail on Applicant > zip: element not found in
time" is one fix, not 18 failures to read.

Each group then says what the evidence points to, and shows the evidence:
- where: one field of one page used by many tests (fix the locator once), or one environment while the others pass
  (the environment, its data or its deploy);
- when: new since a run (the tests passed in the run before it), and what changed in between: the page or test
  files edited after that last passing run;
- how steady: failing in every recent run, or coming and going (flaky).

It reads what runs already keep (results, their steps and the report's failure point), so it works on past runs too.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

FAILED = ("failed", "error")
RECENT = 10

# (kind, label, pattern): the first that matches the message names the error
KINDS = [
    ("unreachable", "site unreachable", r"net::ERR_|ECONNREFUSED|Connection refused|ERR_NAME_NOT_RESOLVED|"
                                         r"getaddrinfo|Name or service not known|ERR_CONNECTION"),
    ("login", "sign-in failed", r"Missing credentials|login failed|could not log in|Invalid (user|password)"),
    ("page-missing", "page file missing", r"Page Config[: ].*not found|Page .*: Component .* not found"),
    ("field-missing", "field not on the page", r"No Field Config for"),
    ("several", "several elements match", r"strict mode violation"),
    ("hidden", "element hidden or disabled", r"not visible|element is not enabled|is hidden|not editable"),
    ("not-found", "element not found in time", r"Timeout \d+ ?ms exceeded|waiting for (locator|get_by|selector)|"
                                               r"element\(s\) not found|Element not found|not ready|"
                                               r"check_element_ready"),
    ("text", "expected text missing", r"Unable to confirm text|to_contain_text|to_have_text|expected .* text"),
    ("navigation", "page did not load", r"page\.goto|navigation|ERR_ABORTED|net::ERR_TIMED_OUT"),
    ("data", "test data problem", r"autoGen|rand_gen|data set|DATA\.|Dictionary key"),
    ("assertion", "check failed", r"AssertionError|assert "),
]
_COMPILED = [(k, label, re.compile(p, re.I)) for k, label, p in KINDS]
_NOISE = [(re.compile(r"https?://\S+"), "<url>"), (re.compile(r"(['\"]).*?\1"), "'…'"),
          (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "<id>"), (re.compile(r"\d+(\.\d+)?"), "N"),
          (re.compile(r"\s+"), " ")]


def error_kind(message: str) -> tuple[str, str]:
    """(kind, label) of an error message: a named kind, or "other" with the message's first line, masked."""
    text = str(message or "")
    for kind, label, pattern in _COMPILED:
        if pattern.search(text):
            return kind, label
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first = re.sub(r"^(E\s+|Failed: |Failure: )", "", first)
    for pattern, repl in _NOISE:
        first = pattern.sub(repl, first)
    return "other", (first[:90] or "failed without a message")


@dataclass
class Failure:
    run: str
    at: str
    env: str
    test_id: str
    browser: str
    screen: str
    field: str
    action: str
    kind: str
    label: str
    message: str

    @property
    def where(self) -> str:
        return " > ".join(x for x in (self.screen, self.field) if x)


@dataclass
class Group:
    key: str
    kind: str
    label: str
    screen: str
    field: str
    failures: list[Failure] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)  # what the evidence points to, most telling first
    suggestion: str = ""
    new_since: str = ""  # the run since which these tests fail (they passed in the run before it)
    changes: list[dict] = field(default_factory=list)  # {file, at}: edits after the last passing run
    steady: bool = False  # failing in every recent run of these tests
    envs: list[str] = field(default_factory=list)
    passing_envs: list[str] = field(default_factory=list)

    @property
    def where(self) -> str:
        return " > ".join(x for x in (self.screen, self.field) if x) or "(where is unknown)"

    @property
    def tests(self) -> list[str]:
        return sorted({f.test_id for f in self.failures})

    @property
    def runs(self) -> list[str]:
        return sorted({f.run for f in self.failures}, reverse=True)

    @property
    def examples_message(self) -> str:
        return next((f.message.strip()[:1200] for f in self.failures if f.message.strip()), "")

    @property
    def title(self) -> str:
        n = len(self.tests)
        return f"{n} test{'' if n == 1 else 's'} fail{'s' if n == 1 else ''} on {self.where}: {self.label}"

    def to_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "kind": self.kind, "label": self.label, "where": self.where,
                "screen": self.screen, "field": self.field, "tests": self.tests, "runs": self.runs,
                "failures": len(self.failures), "explanation": self.explanation, "suggestion": self.suggestion,
                "new_since": self.new_since, "changes": self.changes, "steady": self.steady, "envs": self.envs,
                "passing_envs": self.passing_envs,
                "examples": [{"run": f.run, "test_id": f.test_id, "browser": f.browser, "message": f.message[:400]}
                             for f in self.failures[:3]]}


def failures_of(run, points: dict | None = None) -> list[Failure]:
    """The failed results of a run, each with where it failed: the report's failure point (`points`:
    (test_id, browser) -> TestDetails) or else the failed step the runner recorded."""
    out = []
    for r in run.results.values():
        if r.outcome not in FAILED:
            continue
        detail = (points or {}).get((r.test_id, r.browser))
        point = getattr(detail, "failure", None)
        screen, fname, action = (point.screen, point.field_name, point.action) if point else ("", "", "")
        message = r.message or str(getattr(detail, "message", "") or "")
        if not screen:
            step = next((s for s in r.steps if s.get("status") == "failed"), None)
            if step:
                label = str(step.get("label") or "")
                if step.get("kind") == "screen":
                    screen = label
                message = message or str(step.get("error") or "")
        kind, label = error_kind(message)
        out.append(Failure(run.id, run.created_at, run.config.env, r.test_id, r.browser, screen, fname, action,
                           kind, label, message))
    return out


def _signature(f: Failure) -> str:
    return "|".join((f.screen, f.field, f.kind, f.label if f.kind == "other" else ""))


def _parse(at: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(at).replace("Z", ""))
    except ValueError:
        return None


class FailureIntel:
    """Groups and explains failures. `details(run)` gives a run's report details (RunArtifacts); `changed(system,
    screen, test_id)` gives the files behind a failure, each as (label, [edit times]) for the change check."""

    def __init__(self, runs: Callable[[str, str | None], list], details: Callable[[object], dict],
                 changed: Callable[[str, str, str], list[tuple[str, list[str]]]] | None = None):
        self.runs, self.details, self.changed = runs, details, changed or (lambda s, p, t: [])

    def groups(self, system: str, env: str | None = None, limit: int = RECENT,
               run_id: str | None = None) -> list[Group]:
        """The failure groups of the latest `limit` finished runs of a system (one environment, or all), or of one
        run (`run_id`) explained against those recent runs. Biggest groups first."""
        recent = [r for r in self.runs(system, env) if r.finished_at][:limit]
        if run_id:
            target = next((r for r in recent if r.id == run_id), None)
            if target is None:
                target = next((r for r in self.runs(system, None) if r.id == run_id), None)
                if target is None:
                    return []
                recent = [target, *[r for r in recent if r.created_at < target.created_at]][:limit]
            scope = [target]
        else:
            scope = recent
        failures = [f for run in scope for f in failures_of(run, self._details(run))]
        grouped: dict[str, Group] = {}
        for f in failures:
            key = _signature(f)
            grouped.setdefault(key, Group(key, f.kind, f.label, f.screen, f.field)).failures.append(f)
        # the same tests elsewhere: everything the recent runs say about each test, newest first
        every_env = [r for r in self.runs(system, None) if r.finished_at][:limit * 3]
        outcomes: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)  # test -> (at, run, env, outcome)
        for run in {r.id: r for r in [*recent, *every_env]}.values():
            for r in run.results.values():
                outcomes[r.test_id].append((run.created_at, run.id, run.config.env, r.outcome))
        for items in outcomes.values():
            items.sort(reverse=True)
        out = [self._explain(g, outcomes, system) for g in grouped.values()]
        out.sort(key=lambda g: (-len(g.tests), -len(g.failures), g.where))
        return out

    def _details(self, run) -> dict:
        try:
            return self.details(run) or {}
        except Exception:  # noqa: BLE001 - a run without a readable report still has its results
            return {}

    def _explain(self, g: Group, outcomes: dict, system: str) -> Group:
        tests = g.tests
        g.envs = sorted({f.env for f in g.failures})
        latest_fail = max(f.at for f in g.failures)[:19]
        first_fail = min(f.at for f in g.failures)
        passing = set()
        for t in tests:
            for at, _, env, outcome in outcomes.get(t, []):
                if env not in g.envs and outcome == "passed" and at >= _day_before(first_fail):
                    passing.add(env)
        g.passing_envs = sorted(passing)

        # how steady: these tests' results in the failing environments, newest first
        history = [(at, run, outcome) for t in tests for at, run, env, outcome in outcomes.get(t, [])
                   if env in g.envs]
        history.sort(reverse=True)
        verdicts: dict[str, set] = defaultdict(set)
        for at, run, outcome in history:
            verdicts[run].add("failed" if outcome in FAILED else outcome)
        ordered = sorted({(at, run) for at, run, _ in history}, reverse=True)
        streak, last_pass = [], None
        for at, run in ordered:
            if verdicts[run] & {"failed"}:
                streak.append((at, run))
            elif "passed" in verdicts[run]:
                last_pass = (at, run)
                break
        flips = sum(1 for (a, b) in zip(ordered, ordered[1:])
                    if bool(verdicts[a[1]] & {"failed"}) != bool(verdicts[b[1]] & {"failed"}))
        g.steady = last_pass is None or flips <= 1
        if streak and last_pass:
            g.new_since = streak[-1][1]

        # what changed after the last passing run (or ever, when they never passed in these runs)
        since = last_pass[0][:19] if last_pass else ""
        bound = streak[-1][0][:19] if streak else latest_fail  # up to the first run that failed
        seen_files = set()
        for f in g.failures:
            for label, times in self.changed(system, f.screen, f.test_id):
                if label in seen_files:
                    continue
                seen_files.add(label)
                edits = sorted(t for t in times if since and since < t <= bound)
                if edits:
                    g.changes.append({"file": label, "at": edits[-1].replace("T", " ")[:16]})

        # the explanation, most telling first
        n = len(tests)
        if g.kind == "unreachable":
            g.explanation.append(f"The site did not answer in {', '.join(g.envs)}: nothing about the tests changed.")
            g.suggestion = f"Check that {', '.join(g.envs)} is up and reachable from where the tests run."
        elif g.kind == "login":
            g.explanation.append("Sign-in failed before the tests could start.")
            g.suggestion = "Check the system's credentials for this environment (.env, Login credentials, vault)."
        if g.passing_envs and not g.kind == "unreachable":
            g.explanation.append(f"Only {', '.join(g.envs)}: the same tests pass in {', '.join(g.passing_envs)}. "
                                 "Look at the environment, its data or its latest deploy before the tests.")
        if g.field and n > 1:
            g.explanation.append(f"One field, {g.where}, in {n} tests: one fix on the page mends them all.")
        if g.kind in ("not-found", "hidden", "several") and g.field and not g.suggestion:
            g.suggestion = {"not-found": f"Check {g.field}'s locator on {g.screen} (Generator > Pages > Maintain > "
                                         "Check all locators); the page may have changed.",
                            "hidden": f"{g.field} is on {g.screen} but hidden or disabled when the step runs: an "
                                      "earlier step may not have finished, or the page shows it later.",
                            "several": f"{g.field}'s locator on {g.screen} matches several elements: make it "
                                       "specific, or set index."}[g.kind]
        elif g.kind == "page-missing":
            g.suggestion = f"The page {g.screen or ''} (or a component it uses) is missing from Pages."
        elif g.kind == "field-missing":
            g.suggestion = "A step uses a field the page does not have: rename it in the step or add it to the page."
        elif g.kind == "text" and not g.suggestion:
            g.suggestion = "The expected text is not on the page: the page's wording or the test's expectation changed."
        if g.new_since:
            when = (_parse(streak[-1][0]) or datetime.min).strftime("%Y-%m-%d %H:%M")
            g.explanation.append(f"New since run {g.new_since} ({when}): these tests passed in the run before.")
        for c in g.changes:
            g.explanation.append(f"{c['file']} was edited at {c['at']}, after the last run where these tests passed.")
        if not g.steady:
            g.explanation.append("Comes and goes: these tests also passed in between (flaky, or timing).")
        elif not g.new_since and len(ordered) > 1:
            g.explanation.append(f"Failing in every one of the last {min(len(ordered), RECENT)} runs of these tests.")
        return g


def _day_before(at: str) -> str:
    return str(at)[:10]


def file_changes(workspace: Path, models, history_versions) -> Callable[[str, str, str], list[tuple[str, list[str]]]]:
    """The change check for FailureIntel: the page file of the failing screen and the test case's file, each with
    the times it was saved (its earlier versions, from the edit history, and its last change on disk)."""
    workspace = Path(workspace)

    def times(path: Path) -> list[str]:
        out = []
        try:
            out.append(datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S"))
        except OSError:
            return []
        try:
            out += [v.at.replace(" ", "T") for v in history_versions(path)]
        except Exception:  # noqa: BLE001 - the edit history is a hint
            pass
        return out

    def changed(system: str, screen: str, test_id: str) -> list[tuple[str, list[str]]]:
        out = []
        snap = models()
        if screen:
            rel = snap.page_files.get((system, screen)) if snap is not None else None
            if rel:
                out.append((f"Pages/{system}/{rel}", times(workspace / "Pages" / system / rel)))
        node = snap.tests.get((system, test_id)) if snap is not None else None
        if node is not None:
            out.append((f"Tests/{node.folder}/{node.rel}", times(workspace / "Tests" / node.folder / node.rel)))
        return out
    return changed


def summary(groups: Iterable[Group]) -> dict:
    groups = list(groups)
    return {"groups": len(groups), "failures": sum(len(g.failures) for g in groups),
            "tests": len({t for g in groups for t in g.tests})}


def for_services(svc) -> FailureIntel:
    """The app's FailureIntel: its runs, their report details, and the workspace's files and edit history."""
    from webapp.services import history
    from webapp.services.workspace_model import model_for

    def runs(system: str, env: str | None) -> list:
        return svc.runs.query(system, env, finished_only=True, limit=RECENT * 3)

    def models():
        try:
            return model_for(svc.settings.workspace, svc.tests).snapshot()
        except Exception:  # noqa: BLE001 - without the model there is just no change check
            return None

    return FailureIntel(runs, lambda run: svc.artifacts.details(run.id, run.results_dir),
                        file_changes(svc.settings.workspace, models, history.versions))
