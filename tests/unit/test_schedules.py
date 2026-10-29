"""Schedules: validation, the JSON store, the scheduler's firing rules, notifications on finish."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from webapp.services.notifier import Notifier
from webapp.services.run_manager import Run, RunConfig
from webapp.services.run_manager import TestResult as Result  # pytest collects Test* names
from webapp.services.schedules import Scheduler, ScheduleStore, validate

BASE = {"name": "Smoke", "cron": "30 6 * * 1-5", "system": "SYS", "env": "dev", "tags": ["smoke"]}


def test_validate_normalizes_and_rejects():
    s = validate({**BASE, "cron": " 30  6 * * 1-5 ", "browsers": ["Chromium"], "channels": [
        {"type": "slack"}, {"type": "email", "to": "a@x.test, b@x.test"}]})
    assert s.cron == "30 6 * * 1-5" and s.browsers == ["chromium"] and s.id.startswith("smoke-")
    assert s.channels == [{"type": "slack", "url_env": "NOTIFY_SLACK_URL"},
                          {"type": "email", "to": ["a@x.test", "b@x.test"]}]
    for bad, msg in [({"name": ""}, "name"), ({"cron": "nope"}, "five fields"), ({"tags": []}, "tag"),
                     ({"system": "../x"}, "system"), ({"options": {"PATH": "x"}}, "Unsupported"),
                     ({"notify_when": "sometimes"}, "Notify"),
                     ({"channels": [{"type": "slack", "url_env": "DB_PASSWORD"}]}, "NOTIFY_"),
                     ({"channels": [{"type": "email", "to": "not-an-address"}]}, "not valid"),
                     ({"channels": [{"type": "fax"}]}, "Unknown"), ({"id": "../../etc"}, "id")]:
        with pytest.raises(ValueError, match=msg):
            validate({**BASE, **bad})


def test_store_round_trip_and_skips_invalid_entries(tmp_path):
    store = ScheduleStore(tmp_path / "Data" / "schedules.json")
    assert store.list() == []
    a = store.save(validate({**BASE, "name": "B nightly"}))
    b = store.save(validate({**BASE, "name": "A hourly", "cron": "0 * * * *"}))
    assert [s.name for s in store.list()] == ["A hourly", "B nightly"]
    store.save(validate({**a.to_dict(), "enabled": False}))
    assert store.get(a.id).enabled is False and len(store.list()) == 2
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["schedules"].append({"name": "broken", "cron": "x"})
    store.path.write_text(json.dumps(data), encoding="utf-8")
    assert len(store.list()) == 2
    assert store.delete(b.id) and not store.delete(b.id)


@dataclass
class FakeRuns:
    """Just enough of RunManager for the scheduler."""
    runs: list[Run] = field(default_factory=list)
    started: list[tuple] = field(default_factory=list)
    listeners: list = field(default_factory=list)
    busy: bool = False

    def list(self) -> list[Run]:
        return sorted(self.runs, key=lambda r: r.created_at, reverse=True)

    def start(self, config, expected=0, trigger=""):
        run = Run(id=f"r{len(self.runs)}", config=config, dir=Path("."), status="running", trigger=trigger,
                  created_at=self.now.isoformat(timespec="seconds"))
        self.runs.append(run)
        self.started.append((config.tags, expected, trigger))
        return run


class FakeCatalog:
    def load(self, system):
        class C:
            def select(self, tags):
                return [object(), object()]
        return C()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make(tmp_path, now):
    store = ScheduleStore(tmp_path / "schedules.json")
    runs, clock = FakeRuns(), Clock(now)
    runs.now = now
    sent = []
    notifier = Notifier(lambda k: None, "http://app.test")
    notifier.send = lambda msg, channels: sent.append((msg, channels)) or [{"channel": "x", "ok": True, "error": ""}]
    return store, runs, clock, Scheduler(store, runs, FakeCatalog(), notifier, clock=clock), sent


def test_fires_once_per_matching_minute_even_while_another_run_is_busy(tmp_path):
    store, runs, clock, scheduler, _ = make(tmp_path, datetime(2026, 10, 30, 6, 29, 50))
    s = store.save(validate({**BASE, "browsers": ["chromium", "firefox"]}))
    store.save(validate({**BASE, "name": "Paused", "enabled": False}))
    assert scheduler.tick() == []  # 06:29
    clock.now = runs.now = datetime(2026, 10, 30, 6, 30, 5)
    started = scheduler.tick()
    assert len(started) == 1 and runs.started == [(["smoke"], 4, f"schedule:{s.id}")]
    clock.now = datetime(2026, 10, 30, 6, 30, 40)
    assert scheduler.tick() == []  # same minute: not again
    fresh = Scheduler(store, runs, FakeCatalog(), scheduler.notifier, clock=clock)
    assert fresh.tick() == []  # an app restart in the same minute sees the run in the history
    # next weekday: started although run r0 is still running (the RunManager queues it)
    clock.now = runs.now = datetime(2026, 11, 2, 6, 30)
    assert len(scheduler.tick()) == 1 and runs.runs[0].status == "running"
    assert scheduler.next_run(s) == datetime(2026, 11, 3, 6, 30)
    assert [r.id for r in scheduler.last_runs(s)] == ["r1", "r0"]
    runs.runs[1].status = "queued"
    assert scheduler.queued(s)


def test_run_now_and_start_errors(tmp_path):
    store, runs, clock, scheduler, _ = make(tmp_path, datetime(2026, 10, 29, 12, 0))
    s = store.save(validate(BASE))
    run = scheduler.run_now(s.id)
    assert run.trigger == f"schedule:{s.id}"
    assert scheduler.run_now(s.id).id == "r1"  # a second one while the first runs: queued by RunManager
    runs.start = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("20 runs are already waiting"))
    with pytest.raises(ValueError, match="already waiting"):
        scheduler.run_now(s.id)
    assert scheduler.errors[s.id] == "20 runs are already waiting"
    with pytest.raises(ValueError, match="not found"):
        scheduler.run_now("nope")


def finished_run(tmp_path, trigger, status, outcome="failed"):
    run = Run(id="r9", config=RunConfig(system="SYS", env="dev", tags=["smoke"], browsers=["chromium"]),
              dir=tmp_path, status=status, trigger=trigger, created_at="2026-10-30T06:30:00")
    run.results["n"] = Result(nodeid="n", test_id="TC-1", browser="chromium", outcome=outcome, message="Boom\nmore")
    return run


@pytest.mark.parametrize("when, status, notified", [
    ("failure", "failed", True), ("failure", "error", True), ("failure", "passed", False),
    ("failure", "cancelled", False), ("always", "passed", True), ("never", "failed", False)])
def test_notifications_follow_notify_when(tmp_path, when, status, notified):
    store, runs, _, scheduler, sent = make(tmp_path, datetime(2026, 10, 30, 7, 0))
    s = store.save(validate({**BASE, "notify_when": when, "channels": [{"type": "webhook"}]}))
    scheduler.on_run_finished(finished_run(tmp_path, f"schedule:{s.id}", status))
    assert bool(sent) is notified
    if notified:
        assert json.loads((tmp_path / "notifications.json").read_text())[0]["ok"] is True
        assert sent[0][0].title.startswith("Smoke: SYS/dev")
    scheduler.on_run_finished(finished_run(tmp_path, "", "failed"))  # a person's run: never
    assert len(sent) == int(notified)
