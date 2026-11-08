"""Automations: "when something happens, do something", in one place.

A rule says when (a run finished, a deploy was announced, a monitored journey went down or recovered), optionally
narrowed to a system, an environment and an outcome, and what to do then: run test cases by tag, run journeys,
send a notification. Rules live in Data/automations.json:

    {"automations": [{"id": "after-deploy", "name": "Smoke after each deploy", "enabled": true,
                      "when": {"on": "deploy", "system": "PORTAL", "env": "uat"},
                      "do": [{"type": "run_tests", "tags": ["smoke"]},
                             {"type": "notify", "channels": [{"type": "slack"}]}]}]}

An action's system and environment default to the event's (the deploy's, the run's), so one rule serves every
environment it matches. The engine listens on the event bus (webapp/services/events.py) and does a matching rule's
actions on its own thread, so a slow channel never holds up a run. Runs it starts carry the trigger
"automation:<rule id>" and are never themselves matched by "run finished" rules: a rule cannot set off itself or
another rule in a loop. Each firing is kept in reports/automations.jsonl.

Schedules (a time) and monitors (journeys every few minutes) stay where they are; the Automations tab lists them
beside the rules.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from webapp.services.json_files import ConflictError, read_json, write_json

logger = logging.getLogger(__name__)

TRIGGER = "automation:"
WHEN = {"run_finished": "a run finishes", "deploy": "a deploy is announced",
        "journey_down": "a monitored journey goes down", "journey_recovered": "a monitored journey recovers"}
ACTIONS = {"run_tests": "run test cases by tag", "run_journeys": "run journeys", "notify": "send a notification"}
OUTCOMES = ("any", "failed", "passed")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
LOG_KEEP = 500


@dataclass
class Rule:
    id: str
    name: str
    when: dict
    do: list[dict] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def summary(self) -> str:
        w = self.when
        where = "/".join(x for x in (w.get("system") or "any system", w.get("env") or "any environment"))
        outcome = f" ({w.get('status')})" if w.get("on") == "run_finished" and w.get("status", "any") != "any" else ""
        actions = "; ".join(describe_action(a) for a in self.do) or "nothing"
        return f"When {WHEN.get(w.get('on'), w.get('on'))}{outcome} on {where}: {actions}"


def describe_action(action: dict) -> str:
    kind = action.get("type")
    if kind == "run_tests":
        return f"run tests tagged {', '.join(action.get('tags') or []) or '(none)'}"
    if kind == "run_journeys":
        return f"run journeys {', '.join(action.get('journeys') or []) or '(all of the system)'}"
    if kind == "notify":
        return f"notify {', '.join(str(c.get('type')) for c in action.get('channels') or []) or '(no channel)'}"
    return str(kind)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "rule"


def validate(item: dict) -> Rule:
    """A Rule from its JSON, or ValueError saying what is wrong."""
    if not isinstance(item, dict):
        raise ValueError("A rule must be an object.")
    rid, name = str(item.get("id") or "").strip(), str(item.get("name") or "").strip()
    if not ID_RE.match(rid):
        raise ValueError(f"Rule id {rid!r}: lowercase letters, digits and dashes.")
    if not name:
        raise ValueError("A rule needs a name.")
    when = dict(item.get("when") or {})
    if when.get("on") not in WHEN:
        raise ValueError(f"When: one of {', '.join(WHEN)}.")
    when.setdefault("status", "any")
    if when["status"] not in OUTCOMES:
        raise ValueError(f"Outcome: one of {', '.join(OUTCOMES)}.")
    do = []
    for action in item.get("do") or []:
        action = dict(action)
        if action.get("type") not in ACTIONS:
            raise ValueError(f"Action: one of {', '.join(ACTIONS)}.")
        if action["type"] == "run_tests" and not [t for t in action.get("tags") or [] if str(t).strip()]:
            raise ValueError("Run tests needs at least one tag.")
        if action["type"] == "notify":
            from webapp.services.notifier import validate_channel
            action["channels"] = [validate_channel(c) for c in action.get("channels") or []]
            if not action["channels"]:
                raise ValueError("Notify needs at least one channel.")
        do.append(action)
    if not do:
        raise ValueError("A rule needs at least one action.")
    return Rule(rid, name, when, do, bool(item.get("enabled", True)))


def matches(rule: Rule, event: dict) -> bool:
    """Does this event (from the event bus, or a deploy) set off this rule?"""
    if not rule.enabled:
        return False
    w, topic = rule.when, event.get("topic")
    on = w.get("on")
    if on == "run_finished":
        if topic != "run" or event.get("change") != "finished":
            return False
        if str(event.get("trigger") or "").startswith(TRIGGER):
            return False  # runs started by automations never set rules off: no loops
        status = w.get("status", "any")
        if status == "failed" and event.get("status") not in ("failed", "error"):
            return False
        if status == "passed" and event.get("status") != "passed":
            return False
    elif on == "deploy":
        if topic != "deploy":
            return False
    elif on in ("journey_down", "journey_recovered"):
        if topic != "monitor" or event.get("change") != "checked":
            return False
        wanted = "down" if on == "journey_down" else "recovered"
        if not any(c.get("state") == wanted for c in event.get("changes") or []):
            return False
    else:
        return False
    return all(not w.get(k) or w[k] == event.get(k) for k in ("system", "env"))


class AutomationStore:
    """Data/automations.json; every write is atomic and checked against the version read."""

    def __init__(self, workspace: Path):
        self.path = Path(workspace) / "Data" / "automations.json"
        self._lock = threading.Lock()

    def _read(self) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            return [], None
        data, sha, _ = read_json(self.path)
        items = data.get("automations") if isinstance(data, dict) else None
        return (items if isinstance(items, list) else []), sha

    def list(self) -> list[Rule]:
        out = []
        for item in self._read()[0]:
            try:
                out.append(validate(item))
            except (ValueError, TypeError) as e:
                logger.warning("Skipping invalid automation %r: %s", item.get("name") if isinstance(item, dict)
                               else item, e)
        return out

    def get(self, rid: str) -> Rule | None:
        return next((r for r in self.list() if r.id == rid), None)

    def save(self, rule: Rule, new: bool = False) -> Rule:
        rule = validate(rule.to_dict())
        with self._lock:
            items, sha = self._read()
            if new and any(isinstance(i, dict) and i.get("id") == rule.id for i in items):
                raise ConflictError(f"A rule with id {rule.id!r} exists already.")
            items = [i for i in items if not (isinstance(i, dict) and i.get("id") == rule.id)]
            items.append(rule.to_dict())
            items.sort(key=lambda i: str(i.get("name", "")).casefold())
            write_json(self.path, {"automations": items}, sha, indent="  ")
        return rule

    def delete(self, rid: str) -> bool:
        with self._lock:
            items, sha = self._read()
            kept = [i for i in items if not (isinstance(i, dict) and i.get("id") == rid)]
            if len(kept) == len(items):
                return False
            write_json(self.path, {"automations": kept}, sha, indent="  ")
        return True


class AutomationEngine:
    """Hears events, finds the rules they set off, and does their actions on its own thread."""

    def __init__(self, store: AutomationStore, log_path: Path, *,
                 start_tests: Callable[[str, str, list[str], str], object],
                 start_journeys: Callable[[str, list[str], str, str], object],
                 notify: Callable[[Rule, dict, list[dict]], list[dict]]):
        self.store, self.log_path = store, Path(log_path)
        self.start_tests, self.start_journeys, self.notify = start_tests, start_journeys, notify
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    # ───── hearing ─────

    def on_event(self, event: dict) -> None:
        """Event bus subscriber: cheap, on the publisher's thread; the work happens on the engine's own."""
        if event.get("topic") not in ("run", "monitor", "deploy"):
            return
        if event.get("topic") == "run" and event.get("change") != "finished":
            return
        if event.get("topic") == "monitor" and not event.get("changes"):
            return
        self._queue.put(dict(event))
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="automations", daemon=True)
                self._worker.start()

    def _work(self) -> None:
        while True:
            try:
                event = self._queue.get(timeout=30)
            except queue.Empty:
                return
            try:
                self.handle(event)
            except Exception:  # noqa: BLE001 - one bad rule must not stop the others
                logger.exception("Automation failed on %s", event.get("topic"))
            finally:
                self._queue.task_done()

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """For tests: until every queued event has been handled."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.05)
        return False

    # ───── doing ─────

    def handle(self, event: dict) -> list[dict]:
        """Every rule this event sets off, fired in name order. Returns the log entries."""
        return [self.fire(rule, event) for rule in self.store.list() if matches(rule, event)]

    def fire(self, rule: Rule, event: dict) -> dict:
        system = event.get("system") or rule.when.get("system") or ""
        env = event.get("env") or rule.when.get("env") or ""
        results = []
        for action in rule.do:
            kind = action.get("type")
            a_system, a_env = action.get("system") or system, action.get("env") or env
            try:
                if kind == "run_tests":
                    run = self.start_tests(a_system, a_env, list(action.get("tags") or []), TRIGGER + rule.id)
                    results.append({"action": kind, "ok": True, "run": getattr(run, "id", "")})
                elif kind == "run_journeys":
                    run = self.start_journeys(a_system, list(action.get("journeys") or []), a_env, TRIGGER + rule.id)
                    results.append({"action": kind, "ok": True, "run": getattr(run, "id", "")})
                elif kind == "notify":
                    sent = self.notify(rule, event, action.get("channels") or [])
                    results.append({"action": kind, "ok": all(s.get("ok") for s in sent), "sent": sent})
            except Exception as e:  # noqa: BLE001 - say what failed; the next action still runs
                results.append({"action": kind, "ok": False, "error": str(e)})
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rule": rule.id, "name": rule.name,
                 "event": {k: event.get(k) for k in ("topic", "change", "id", "status", "system", "env", "build",
                                                    "by") if event.get(k) not in (None, "")},
                 "results": results}
        self._log(entry)
        logger.info("Automation %s fired: %s", rule.name, results)
        return entry

    def _log(self, entry: dict) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Could not write the automations log", exc_info=True)

    def recent(self, limit: int = 50, rule: str | None = None) -> list[dict]:
        """The latest firings, newest first."""
        if not self.log_path.is_file():
            return []
        out = []
        for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_KEEP:]:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if rule is None or item.get("rule") == rule:
                out.append(item)
        return list(reversed(out))[:limit]
