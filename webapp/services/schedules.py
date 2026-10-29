"""Scheduled runs: definitions in <workspace>/Data/schedules.json, fired by a background thread.

Schedules only fire while the web app is running; a time missed while it was stopped is not caught up.
When a run is already in progress, a due schedule's run waits in the RunManager's queue.
A schedule's last runs are its runs in the history (trigger "schedule:<id>"), so no extra state is stored.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from webapp.services.cron import Cron, CronError
from webapp.services.env_config import RUN_OPTION_KEYS
from webapp.services.json_files import NAME_RE, ConflictError, read_json, write_json
from webapp.services.notifier import Notifier, build_message, validate_channel

if TYPE_CHECKING:
    from webapp.services.run_manager import Run, RunManager
    from webapp.services.test_catalog import TestCatalogService

logger = logging.getLogger(__name__)

NOTIFY_WHEN = ("failure", "always", "never")
TICK_SECONDS = 15
TRIGGER = "schedule:"


@dataclass
class Schedule:
    id: str
    name: str
    cron: str
    system: str
    env: str
    tags: list[str]
    browsers: list[str] = field(default_factory=lambda: ["chromium"])
    workers: int = 1
    options: dict = field(default_factory=dict)
    test_ids: list[str] = field(default_factory=list)
    enabled: bool = True
    notify_when: str = "failure"  # failure | always | never
    channels: list[dict] = field(default_factory=list)

    @property
    def trigger(self) -> str:
        return TRIGGER + self.id

    def to_dict(self) -> dict:
        return asdict(self)


def validate(data: dict) -> Schedule:
    """A clean Schedule from user/API input, or ValueError with a readable reason."""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Give the schedule a name.")
    try:
        cron = Cron.parse(str(data.get("cron") or "")).expression
    except CronError as e:
        raise ValueError(f"Schedule: {e}") from e
    system = str(data.get("system") or "").strip()
    if not system or not NAME_RE.match(system) or system in (".", ".."):
        raise ValueError("Pick a system.")
    tags = [str(t).strip() for t in data.get("tags") or [] if str(t).strip()]
    if not tags:
        raise ValueError("Pick at least one tag - the framework runs only tagged tests.")
    browsers = [str(b).strip().lower() for b in data.get("browsers") or [] if str(b).strip()] or ["chromium"]
    options = dict(data.get("options") or {})
    unknown = sorted(k for k in options if k not in RUN_OPTION_KEYS)
    if unknown:
        raise ValueError(f"Unsupported run options: {', '.join(unknown)}")
    when = str(data.get("notify_when") or "failure")
    if when not in NOTIFY_WHEN:
        raise ValueError(f"Notify: use one of {', '.join(NOTIFY_WHEN)}.")
    channels = [validate_channel(c) for c in data.get("channels") or []]
    try:
        workers = max(1, min(16, int(data.get("workers") or 1)))
    except (TypeError, ValueError) as e:
        raise ValueError("Workers must be a number.") from e
    sid = str(data.get("id") or "").strip()
    if sid and not re.match(r"^[a-z0-9-]{1,64}$", sid):
        raise ValueError("Invalid schedule id.")
    return Schedule(id=sid or _new_id(name), name=name[:80], cron=cron, system=system,
                    env=str(data.get("env") or "").strip(), tags=tags, browsers=browsers, workers=workers,
                    options=options, test_ids=[str(t) for t in data.get("test_ids") or []],
                    enabled=bool(data.get("enabled", True)), notify_when=when, channels=channels)


def _new_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "schedule"
    return f"{slug}-{secrets.token_hex(2)}"


class ScheduleStore:
    """schedules.json: {"schedules": [...]}; every write is atomic and checked against the version read."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> tuple[list[dict], str | None]:
        if not self.path.is_file():
            return [], None
        data, sha, _ = read_json(self.path)
        items = data.get("schedules") if isinstance(data, dict) else None
        return (items if isinstance(items, list) else []), sha

    def list(self) -> list[Schedule]:
        out = []
        for item in self._read()[0]:
            try:
                out.append(validate(item))
            except (ValueError, TypeError, AttributeError) as e:
                name = item.get("name") if isinstance(item, dict) else item
                logger.warning("Skipping invalid schedule %r: %s", name, e)
        return out

    def get(self, sid: str) -> Schedule | None:
        return next((s for s in self.list() if s.id == sid), None)

    def save(self, schedule: Schedule) -> Schedule:
        with self._lock:
            items, sha = self._read()
            items = [i for i in items if not (isinstance(i, dict) and i.get("id") == schedule.id)]
            items.append(schedule.to_dict())
            items.sort(key=lambda i: str(i.get("name", "")).casefold())
            write_json(self.path, {"schedules": items}, sha, indent="  ")
        return schedule

    def delete(self, sid: str) -> bool:
        with self._lock:
            items, sha = self._read()
            kept = [i for i in items if not (isinstance(i, dict) and i.get("id") == sid)]
            if len(kept) == len(items):
                return False
            write_json(self.path, {"schedules": kept}, sha, indent="  ")
        return True


class Scheduler:
    """Fires due schedules (checked every TICK_SECONDS) and sends their notifications when runs finish."""

    def __init__(self, store: ScheduleStore, runs: RunManager, catalog: TestCatalogService, notifier: Notifier,
                 clock: Callable[[], datetime] = datetime.now):
        self.store, self.runs, self.catalog, self.notifier, self.clock = store, runs, catalog, notifier, clock
        self.errors: dict[str, str] = {}  # schedule id -> why its last start failed
        self._fired: dict[str, str] = {}  # schedule id -> minute it last fired ("YYYY-MM-DDTHH:MM")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        runs.listeners.append(self.on_run_finished)

    # ───── loop ─────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            self._stop.wait(TICK_SECONDS - time.time() % TICK_SECONDS)

    def tick(self) -> list[Run]:
        """Start what is due now. Returns the runs started (for tests)."""
        now = self.clock()
        minute = now.strftime("%Y-%m-%dT%H:%M")
        started = []
        with self._lock:
            for schedule in self.store.list():
                if not schedule.enabled or self._fired.get(schedule.id) == minute:
                    continue
                if not Cron.parse(schedule.cron).matches(now):
                    continue
                self._fired[schedule.id] = minute
                if any(r.trigger == schedule.trigger and r.created_at[:16] == minute for r in self.runs.list()):
                    continue  # already fired this minute (e.g. the app restarted)
                run = self._start(schedule)  # queued by the RunManager when another run is in progress
                if run:
                    started.append(run)
        return started

    def run_now(self, sid: str) -> Run:
        """Start a schedule now (it waits in the run queue if another run is in progress)."""
        schedule = self.store.get(sid)
        if not schedule:
            raise ValueError("Schedule not found.")
        with self._lock:
            run = self._start(schedule)
        if run is None:
            raise ValueError(self.errors.get(sid, "The run could not be started."))
        return run

    def _start(self, schedule: Schedule) -> Run | None:
        from webapp.services.run_manager import RunConfig
        try:
            selected = self.catalog.load(schedule.system).select(schedule.tags)
            if schedule.test_ids:
                selected = [t for t in selected if t.test_id in set(schedule.test_ids)]
            config = RunConfig(system=schedule.system, env=schedule.env, tags=list(schedule.tags),
                               browsers=list(schedule.browsers), workers=schedule.workers,
                               options=dict(schedule.options), test_ids=list(schedule.test_ids))
            run = self.runs.start(config, expected=len(selected) * len(schedule.browsers), trigger=schedule.trigger)
        except (ValueError, RuntimeError, OSError) as e:
            self.errors[schedule.id] = str(e)
            logger.warning("Schedule %s could not start: %s", schedule.name, e)
            return None
        self.errors.pop(schedule.id, None)
        logger.info("Schedule %s started run %s", schedule.name, run.id)
        return run

    # ───── queries ─────

    def next_run(self, schedule: Schedule) -> datetime | None:
        return Cron.parse(schedule.cron).next_after(self.clock()) if schedule.enabled else None

    def last_runs(self, schedule: Schedule, limit: int = 10) -> list[Run]:
        return [r for r in self.runs.list() if r.trigger == schedule.trigger][:limit]

    def queued(self, schedule: Schedule) -> bool:
        return any(r.status == "queued" for r in self.last_runs(schedule, 3))

    # ───── notifications ─────

    def on_run_finished(self, run: Run) -> None:
        if not run.trigger.startswith(TRIGGER):
            return
        schedule = self.store.get(run.trigger[len(TRIGGER):])
        if not schedule or not schedule.channels or schedule.notify_when == "never":
            return
        if schedule.notify_when == "failure" and run.status in ("passed", "cancelled"):
            return
        message = build_message(run, self.runs.list(), self.notifier.public_url, schedule.name)
        results = self.notifier.send(message, schedule.channels)
        write_notifications(run, results)

    def send_test(self, schedule: Schedule) -> list[dict]:
        return self.notifier.send(self.notifier.test_message(schedule.name), schedule.channels)


def write_notifications(run: Run, results: list[dict]) -> None:
    try:
        (run.dir / "notifications.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("Could not record notifications for %s", run.id)


def read_notifications(run: Run) -> list[dict]:
    try:
        data, _, _ = read_json(run.dir / "notifications.json")
    except (OSError, ValueError, ConflictError):
        return []
    return data if isinstance(data, list) else []
