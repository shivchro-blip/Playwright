"""Monitors: definitions, when a check is due, states per check, incidents, uptime and alerts on change only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from webapp.services.journeys import JourneyStore, validate as journey
from webapp.services.monitors import (
    History, MonitorError, MonitorService, MonitorStore, health, record_check, validate,
)
from webapp.services.run_manager import Run, RunConfig, TestResult


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "Pages").mkdir()
    store = JourneyStore(tmp_path)
    store.save(journey({"name": "Search", "system": "S", "tests": ["TC-1"], "monitor_safe": True}))
    store.save(journey({"name": "Login", "system": "S", "tests": ["TC-2"], "monitor_safe": True}))
    store.save(journey({"name": "File a report", "system": "S", "tests": ["TC-3"]}))
    return tmp_path


class FakeRuns:
    def __init__(self):
        self.listeners, self.items = [], []

    def list(self):
        return list(self.items)


class FakeNotifier:
    public_url = "http://autoplay.local"

    def __init__(self):
        self.sent = []

    def send(self, msg, channels):
        self.sent.append(msg)
        return [{"channel": "webhook", "ok": True, "error": ""}]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 6, 9, 0, 0)

    def __call__(self):
        return self.now


def service(workspace, tmp_path):
    runs, notifier, clock = FakeRuns(), FakeNotifier(), Clock()
    started = []

    def start(monitor):
        r = Run(id=f"r{len(started) + 1}", config=RunConfig(system=monitor.system, env=monitor.env, tags=["t"]),
                dir=tmp_path, status="running", trigger=f"monitor:{monitor.id}", created_at=clock().isoformat())
        started.append(r)
        runs.items.append(r)
        return r

    journeys = SimpleNamespace(store=JourneyStore(workspace), details_for=lambda run: {})
    svc = MonitorService(MonitorStore(workspace), tmp_path / "monitors", journeys, runs, notifier, start, clock)
    return svc, runs, notifier, clock, started


def finish(svc, run, clock, outcomes):
    run.status = "failed" if "failed" in outcomes.values() else "passed"
    run.finished_at = clock().isoformat(timespec="seconds")
    for i, (test_id, outcome) in enumerate(outcomes.items()):
        run.results[f"n{i}"] = TestResult(f"n{i}", test_id, "chromium", outcome, 1.0)
    for listener in svc.runs.listeners:
        listener(run)


def test_validate_refuses_unsafe_or_unknown_journeys(workspace):
    store = JourneyStore(workspace)
    m = validate({"name": "Prod watch", "system": "S", "env": "prod", "journeys": ["search", "search", "login"],
                  "every": "5", "channels": [{"type": "webhook", "url_env": "NOTIFY_HOOK"}]}, store)
    assert (m.id, m.journeys, m.every, m.enabled) == ("prod-watch", ["search", "login"], 5, True)
    with pytest.raises(MonitorError, match="not marked safe"):
        validate({"name": "x", "system": "S", "env": "prod", "journeys": ["file-a-report"]}, store)
    for bad in ({"name": "", "system": "S", "env": "prod", "journeys": ["search"]},
                {"name": "x", "system": "S", "env": "prod", "journeys": []},
                {"name": "x", "system": "S", "env": "prod", "journeys": ["nope"]},
                {"name": "x", "system": "S", "env": "prod", "journeys": ["search"], "every": 7},
                {"name": "x", "system": "../S", "env": "prod", "journeys": ["search"]}):
        with pytest.raises(MonitorError):
            validate(bad, store)


def test_store_round_trip(workspace):
    store, js = MonitorStore(workspace), JourneyStore(workspace)
    store.save(validate({"name": "Prod", "system": "S", "env": "prod", "journeys": ["search"]}, js))
    twin = store.save(validate({"name": "Prod", "system": "S", "env": "uat", "journeys": ["login"]}, js))
    assert twin.id == "prod-2" and [m.env for m in store.list()] == ["prod", "uat"]
    store.save(validate({"id": "prod-2", "name": "Prod", "system": "S", "env": "uat", "journeys": ["login"],
                         "enabled": False}, js), replaces="prod-2")
    assert store.get("prod-2").enabled is False and len(store.list()) == 2
    assert store.delete("prod") and not store.delete("prod")
    assert json.loads((workspace / "Data" / "monitors.json").read_text())["monitors"][0]["id"] == "prod-2"


def test_record_check_opens_and_closes_incidents():
    data = {"checks": [], "incidents": []}
    assert record_check(data, "2026-11-06T09:00:00", "r1", {"a": "up", "b": "up"}, {}) == []
    assert record_check(data, "2026-11-06T09:05:00", "r2", {"a": "down", "b": "up"}, {"a": "Login: Submit"}) \
        == [("a", "down", "Login: Submit")]
    assert record_check(data, "2026-11-06T09:10:00", "r3", {"a": "down", "b": "unknown"}, {}) == []  # still down
    assert record_check(data, "2026-11-06T09:15:00", "r4", {"a": "unknown", "b": "up"}, {}) == []  # says nothing
    assert record_check(data, "2026-11-06T09:20:00", "r5", {"a": "up", "b": "up"}, {}) \
        == [("a", "recovered", "after 15 min")]
    assert data["incidents"] == [{"journey": "a", "started": "2026-11-06T09:05:00", "ended": "2026-11-06T09:20:00",
                                  "run": "r2", "where": "Login: Submit", "minutes": 15}]
    # first ever check already down: an incident, and an alert
    assert record_check({"checks": [], "incidents": []}, "x", "r", {"a": "down"}, {}) == [("a", "down", "")]


def test_health_uptime_by_window():
    now = datetime(2026, 11, 30, 12, 0, 0)
    data = {"checks": [], "incidents": []}
    old = now - timedelta(days=10)
    for i in range(4):  # 10 days ago: 4 down
        record_check(data, (old + timedelta(minutes=i)).isoformat(), f"o{i}", {"a": "down"}, {})
    for i in range(4):  # today: 3 up, 1 unknown (not counted)
        record_check(data, (now - timedelta(hours=1, minutes=-i)).isoformat(), f"n{i}",
                     {"a": "unknown" if i == 3 else "up"}, {})
    h = health(data, "a", "A", now)
    assert h.uptime == {"24h": 100.0, "7d": 100.0, "30d": 42.86} and h.state == "unknown" and h.checks == 8
    assert health(data, "zzz", "Z", now).state == "never"


def test_due_checks_do_not_pile_up_and_alert_on_change_only(workspace, tmp_path):
    svc, runs, notifier, clock, started = service(workspace, tmp_path)
    js = JourneyStore(workspace)
    svc.store.save(validate({"name": "Prod", "system": "S", "env": "prod", "journeys": ["search", "login"],
                             "every": 5, "channels": [{"type": "webhook", "url_env": "NOTIFY_HOOK"}]}, js))
    svc.store.save(validate({"name": "Off", "system": "S", "env": "uat", "journeys": ["login"],
                             "enabled": False}, js))
    assert len(svc.tick()) == 1  # the disabled one never runs
    clock.now += timedelta(minutes=10)
    assert svc.tick() == []  # the first check is still running: no second one
    finish(svc, started[0], clock, {"TC-1": "passed", "TC-2": "passed"})
    assert notifier.sent == []  # all up from the start: nothing to say
    assert svc.tick() and len(started) == 2  # overdue, so it runs at once
    clock.now += timedelta(minutes=1)
    assert svc.tick() == []
    finish(svc, started[1], clock, {"TC-1": "passed", "TC-2": "failed"})
    msg = notifier.sent[-1]
    assert msg.title == "Prod: DOWN on S/prod" and msg.lines == ["Login is down"] and msg.status == "failed"
    assert msg.data["changes"][0]["journey"] == "login" and msg.url.endswith("/runs/r2/report.html")
    clock.now += timedelta(minutes=5)
    svc.tick()
    finish(svc, started[2], clock, {"TC-1": "passed", "TC-2": "failed"})
    assert len(notifier.sent) == 1  # still down: no repeat alert
    clock.now += timedelta(minutes=5)
    svc.tick()
    finish(svc, started[3], clock, {"TC-1": "passed", "TC-2": "passed"})
    assert notifier.sent[-1].title == "Prod: RECOVERED on S/prod" and notifier.sent[-1].lines == \
        ["Login recovered after 10 min"]
    summary = svc.summary(svc.store.get("prod"))
    login = next(j for j in summary["journeys"] if j["id"] == "login")
    assert (login["state"], login["uptime"]["24h"], login["checks"]) == ("up", 50.0, 4)
    assert summary["incidents"][0]["minutes"] == 10 and summary["last_run"] == "r4"
    assert summary["next_check"] == (clock.now + timedelta(minutes=5)).isoformat(timespec="seconds")


def test_runs_that_say_nothing_are_unknown(workspace, tmp_path):
    svc, runs, notifier, clock, started = service(workspace, tmp_path)
    svc.store.save(validate({"name": "Prod", "system": "S", "env": "prod", "journeys": ["search"]},
                            JourneyStore(workspace)))
    svc.check_now("prod")
    started[0].status = "error"
    started[0].finished_at = clock().isoformat()
    for listener in runs.listeners:
        listener(started[0])
    assert History(svc.history_dir, "prod").load()["checks"][0]["states"] == {"search": "unknown"}
    other = Run(id="x", config=RunConfig(system="S", env="prod", tags=["t"]), dir=tmp_path, status="failed",
                trigger="manual")
    for listener in runs.listeners:
        listener(other)  # not a monitor run: ignored
    assert len(History(svc.history_dir, "prod").load()["checks"]) == 1


def test_a_monitor_that_cannot_start_shows_why(workspace, tmp_path):
    svc, *_ = service(workspace, tmp_path)
    svc.start_run = lambda m: (_ for _ in ()).throw(ValueError("prod is not in systemData.json"))
    svc.store.save(validate({"name": "Prod", "system": "S", "env": "prod", "journeys": ["search"]},
                            JourneyStore(workspace)))
    assert svc.tick() == [] and svc.errors["prod"] == "prod is not in systemData.json"
    assert svc.summary(svc.store.get("prod"))["error"]
    with pytest.raises(MonitorError):
        svc.check_now("gone")
