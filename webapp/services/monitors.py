"""Monitoring: run journeys on a short interval against an environment (production included) and say when they
break and when they recover.

A monitor watches some journeys of a system on one environment every N minutes. Only journeys marked "safe to
monitor" can be watched: their tests must not create real records, because they will run against the live system
many times a day. Each check is one run (trigger monitor:<id>); a new check never starts while the previous one is
still running. After each check the monitor records every journey as up, down or unknown (its tests did not all
run), opens an incident when a journey goes down and closes it when it recovers, and alerts the monitor's channels
on those changes only, not on every failed check.

Definitions: <workspace>/Data/monitors.json. History (checks and incidents): <reports>/monitors/<id>.json.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from webapp.services.events import publish_on
from webapp.services.json_files import NAME_RE, ConflictError, read_json, write_json
from webapp.services.notifier import Message, validate_channel

logger = logging.getLogger(__name__)

TRIGGER = "monitor:"
INTERVALS = (5, 10, 15, 30, 60, 120, 240)
TICK_SECONDS = 30
MAX_CHECKS = 5000
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


class MonitorError(ValueError):
    """Shown to the user as is."""


@dataclass
class Monitor:
    id: str
    name: str
    system: str
    env: str
    journeys: list[str]
    every: int = 15  # minutes
    enabled: bool = True
    channels: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def validate(data: dict, journeys) -> Monitor:
    """A clean monitor; `journeys` is the JourneyStore, to check that each watched journey exists and is safe."""
    if not isinstance(data, dict):
        raise MonitorError("A monitor is an object.")
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 80:
        raise MonitorError("Name the monitor (at most 80 characters), e.g. 'Production filings'.")
    system, env = str(data.get("system") or "").strip(), str(data.get("env") or "").strip()
    if not system or not NAME_RE.match(system):
        raise MonitorError("Pick the system.")
    if not env or not NAME_RE.match(env):
        raise MonitorError("Pick the environment to watch.")
    try:
        every = int(data.get("every") or 15)
    except (TypeError, ValueError):
        raise MonitorError("The interval must be a number of minutes.") from None
    if every not in INTERVALS:
        raise MonitorError(f"Check every {', '.join(map(str, INTERVALS))} minutes.")
    wanted = [str(j) for j in data.get("journeys") or [] if str(j).strip()]
    if not wanted:
        raise MonitorError("Pick at least one journey to watch.")
    known = {j.id: j for j in journeys.list(system)}
    for jid in wanted:
        if jid not in known:
            raise MonitorError(f"No journey {jid!r} in {system}.")
        if not known[jid].monitor_safe:
            raise MonitorError(f"'{known[jid].name}' is not marked safe to monitor. Mark it on the Journeys tab "
                               "only if its tests create no real records.")
    channels = [validate_channel(c) for c in data.get("channels") or []]
    mid = str(data.get("id") or "").strip() or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "monitor"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", mid):
        raise MonitorError("Invalid monitor id.")
    return Monitor(mid, name, system, env, list(dict.fromkeys(wanted)), every, bool(data.get("enabled", True)),
                   channels)


class MonitorStore:
    def __init__(self, workspace: Path):
        self.path = Path(workspace) / "Data" / "monitors.json"

    def _read(self) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            return [], None
        data, sha, _ = read_json(self.path)
        items = data.get("monitors") if isinstance(data, dict) else None
        return (items if isinstance(items, list) else []), sha

    def list(self) -> list[Monitor]:
        try:
            items, _ = self._read()
        except (OSError, ValueError):
            return []
        out = []
        for item in items:
            try:
                out.append(Monitor(**{k: item[k] for k in Monitor.__dataclass_fields__ if k in item}))
            except (TypeError, KeyError):
                logger.warning("Skipping an unreadable monitor: %r", item)
        return sorted(out, key=lambda m: m.name.casefold())

    def get(self, mid: str) -> Monitor | None:
        return next((m for m in self.list() if m.id == mid), None)

    def save(self, monitor: Monitor, replaces: str | None = None) -> Monitor:
        items, sha = self._read()
        others = [i for i in items if not (isinstance(i, dict) and i.get("id") in {replaces} - {None})]
        if replaces is None:
            base, n = monitor.id, 2
            while any(isinstance(i, dict) and i.get("id") == monitor.id for i in others):
                monitor.id, n = f"{base}-{n}", n + 1
        elif any(isinstance(i, dict) and i.get("id") == monitor.id for i in others):
            raise MonitorError(f"Another monitor already has the id {monitor.id!r}; choose another name.")
        others.append(monitor.to_dict())
        try:
            write_json(self.path, {"monitors": sorted(others, key=lambda i: str(i.get("name", "")).casefold())},
                       sha, indent="  ")
        except ConflictError as e:
            raise MonitorError(f"{e} Reopen the Monitors tab and try again.") from e
        return monitor

    def delete(self, mid: str) -> bool:
        items, sha = self._read()
        kept = [i for i in items if not (isinstance(i, dict) and i.get("id") == mid)]
        if len(kept) == len(items):
            return False
        write_json(self.path, {"monitors": kept}, sha, indent="  ")
        return True


# ───────────── history: checks, incidents, uptime ─────────────

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp[:19])
    except (ValueError, TypeError):
        return None


@dataclass
class JourneyHealth:
    id: str
    name: str
    state: str  # up | down | unknown | never
    since: str = ""
    where: str = ""
    uptime: dict = field(default_factory=dict)  # window -> % or None
    checks: int = 0


class History:
    """reports/monitors/<id>.json: {"last_started", "checks": [{at, run, states, where}], "incidents": [...]}."""

    def __init__(self, folder: Path, mid: str):
        self.path = Path(folder) / f"{mid}.json"

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("checks", [])
        data.setdefault("incidents", [])
        data.setdefault("last_started", "")
        return data

    def save(self, data: dict) -> None:
        data["checks"] = data["checks"][-MAX_CHECKS:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def record_check(data: dict, at: str, run_id: str, states: dict[str, str], where: dict[str, str]
                 ) -> list[tuple[str, str, str]]:
    """Add a check and update incidents. Returns the changes: (journey id, "down" | "recovered", detail)."""
    last: dict[str, str] = {}
    for check in data["checks"]:
        for jid, state in check["states"].items():
            if state in ("up", "down"):
                last[jid] = state
    data["checks"].append({"at": at, "run": run_id, "states": states, "where": where})
    changes = []
    for jid, state in states.items():
        before = last.get(jid)
        if state == "down" and before != "down":
            data["incidents"].append({"journey": jid, "started": at, "ended": "", "run": run_id,
                                      "where": where.get(jid, ""), "minutes": None})
            changes.append((jid, "down", where.get(jid, "")))
        elif state == "up" and before == "down":
            for incident in reversed(data["incidents"]):
                if incident["journey"] == jid and not incident["ended"]:
                    incident["ended"] = at
                    start, end = _parse(incident["started"]), _parse(at)
                    incident["minutes"] = round((end - start).total_seconds() / 60) if start and end else None
                    changes.append((jid, "recovered", f"after {incident['minutes']} min"
                                    if incident["minutes"] is not None else ""))
                    break
    return changes


def health(data: dict, journey_id: str, name: str, now: datetime) -> JourneyHealth:
    mine = [c for c in data["checks"] if journey_id in c["states"]]
    out = JourneyHealth(journey_id, name, "never")
    if not mine:
        return out
    out.checks = len(mine)
    current = mine[-1]["states"][journey_id]
    out.state, out.where = current, mine[-1]["where"].get(journey_id, "")
    since = mine[-1]["at"]
    for check in reversed(mine):  # how long it has been in this state
        if check["states"][journey_id] != current:
            break
        since = check["at"]
    out.since = since
    for label, span in WINDOWS.items():
        counted = [c["states"][journey_id] for c in mine
                   if (_parse(c["at"]) or now) >= now - span and c["states"][journey_id] in ("up", "down")]
        out.uptime[label] = round(100 * counted.count("up") / len(counted), 2) if counted else None
    return out


# ───────────── the service ─────────────

class MonitorService:
    def __init__(self, store: MonitorStore, history_dir: Path, journeys, runs, notifier,
                 start_run: Callable[[Monitor], object], clock: Callable[[], datetime] = datetime.now):
        self.store, self.history_dir, self.journeys, self.runs = store, Path(history_dir), journeys, runs
        self.notifier, self.start_run, self.clock = notifier, start_run, clock
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        runs.listeners.append(self.on_run_finished)

    def history(self, mid: str) -> History:
        return History(self.history_dir, mid)

    # ───── firing ─────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitors", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # the loop must survive anything a monitor throws
                logger.exception("Monitor tick failed")
            self._stop.wait(TICK_SECONDS)

    def busy(self, mid: str) -> bool:
        return any(r.active and r.trigger == TRIGGER + mid for r in self.runs.list())

    def due(self, monitor: Monitor) -> bool:
        last = _parse(self.history(monitor.id).load()["last_started"])
        return last is None or self.clock() - last >= timedelta(minutes=monitor.every)

    def tick(self) -> list:
        started = []
        with self._lock:
            for monitor in self.store.list():
                if monitor.enabled and not self.busy(monitor.id) and self.due(monitor):
                    run = self.check_now(monitor.id)
                    if run is not None:
                        started.append(run)
        return started

    def check_now(self, mid: str):
        """Start a check now (skipped, returning None, while the previous one is still running)."""
        monitor = self.store.get(mid)
        if monitor is None:
            raise MonitorError("That monitor no longer exists.")
        if self.busy(mid):
            return None
        hist = self.history(mid)
        data = hist.load()
        data["last_started"] = _iso(self.clock())
        hist.save(data)
        try:
            run = self.start_run(monitor)
        except Exception as e:
            self.errors[mid] = str(e)
            logger.warning("Monitor %s could not start: %s", monitor.name, e)
            publish_on(self.runs, "monitor", id=mid, change="error")
            return None
        self.errors.pop(mid, None)
        publish_on(self.runs, "monitor", id=mid, change="started")
        return run

    # ───── results ─────

    def on_run_finished(self, run) -> None:
        if not run.trigger.startswith(TRIGGER):
            return
        monitor = self.store.get(run.trigger[len(TRIGGER):])
        if monitor is None:
            return
        from webapp.services.journeys import status
        states, where = {}, {}
        for journey in self.journeys.store.list(monitor.system):
            if journey.id not in monitor.journeys:
                continue
            st = status(journey, [run], monitor.env, details_for=self.journeys.details_for, stale_days=36500)
            states[journey.id] = {"passing": "up", "failing": "down"}.get(st.state, "unknown")
            if st.where:
                where[journey.id] = st.where
        if run.status == "error" and not any(s == "down" for s in states.values()):
            states = {jid: "unknown" for jid in states}  # a run that broke says nothing about the journeys
        hist = self.history(monitor.id)
        data = hist.load()
        changes = record_check(data, run.finished_at or _iso(self.clock()), run.id, states, where)
        hist.save(data)
        publish_on(self.runs, "monitor", id=monitor.id, change="checked")
        if changes and monitor.channels:
            names = {j.id: j.name for j in self.journeys.store.list(monitor.system)}
            self.notifier.send(self.alert(monitor, run, changes, names), monitor.channels)

    def alert(self, monitor: Monitor, run, changes, names: dict[str, str]) -> Message:
        down = [c for c in changes if c[1] == "down"]
        base = self.notifier.public_url.rstrip("/") if getattr(self.notifier, "public_url", "") else ""
        url = f"{base}/runs/{run.id}/report.html" if base else ""
        what = "DOWN" if down else "RECOVERED"
        title = f"{monitor.name}: {what} on {monitor.system}/{monitor.env}"
        lines = [f"{names.get(j, j)} is down" + (f" at {d}" if d else "") if kind == "down"
                 else f"{names.get(j, j)} recovered {d}".rstrip() for j, kind, d in changes]
        data = {"event": "monitor.change", "monitor": monitor.id, "system": monitor.system, "env": monitor.env,
                "run": run.id, "report_url": url,
                "changes": [{"journey": j, "name": names.get(j, j), "change": kind, "detail": d}
                            for j, kind, d in changes]}
        return Message(title, "failed" if down else "passed", lines,
                       [("Monitor", monitor.name), ("Environment", f"{monitor.system}/{monitor.env}"),
                        ("Run", run.id)], [], url, data)

    # ───── reading ─────

    def summary(self, monitor: Monitor) -> dict:
        data = self.history(monitor.id).load()
        now = self.clock()
        names = {j.id: j.name for j in self.journeys.store.list(monitor.system)}
        last = data["checks"][-1] if data["checks"] else None
        started = _parse(data["last_started"])
        return {"monitor": monitor.to_dict(), "busy": self.busy(monitor.id), "error": self.errors.get(monitor.id, ""),
                "last_check": last["at"] if last else "", "last_run": last["run"] if last else "",
                "next_check": _iso(started + timedelta(minutes=monitor.every)) if started and monitor.enabled else "",
                "journeys": [asdict(health(data, jid, names.get(jid, jid), now)) for jid in monitor.journeys],
                "incidents": list(reversed(data["incidents"]))[:20]}
