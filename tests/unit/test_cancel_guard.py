"""Cancelling a run goes through the same permission check and audit from Control and from the Generator."""
from __future__ import annotations

from types import SimpleNamespace

from webapp.ui import run_actions


def stub(monkeypatch, allowed: bool, cancels: bool = True):
    seen = {"guard": [], "audit": [], "cancelled": [], "notify": []}
    run = SimpleNamespace(config=SimpleNamespace(system="PORTAL", env="prod"))
    runs = SimpleNamespace(get=lambda rid: run if rid == "r1" else None,
                           cancel=lambda rid: seen["cancelled"].append(rid) or cancels)
    monkeypatch.setattr(run_actions, "services", lambda: SimpleNamespace(runs=runs))
    monkeypatch.setattr(run_actions, "guard", lambda *a, **k: seen["guard"].append((a, k)) or allowed)
    monkeypatch.setattr(run_actions, "audit", lambda *a, **k: seen["audit"].append((a, k)))
    monkeypatch.setattr(run_actions.ui, "notify", lambda *a, **k: seen["notify"].append(a))
    return seen


def test_someone_who_may_not_run_tests_there_cannot_cancel_and_nothing_stops(monkeypatch):
    seen = stub(monkeypatch, allowed=False)
    assert run_actions.cancel_run("r1") is False
    assert seen["cancelled"] == [] and seen["audit"] == []
    (args, kwargs), = seen["guard"]  # asked about this system and environment, for this action
    assert args[0] == "run" and kwargs == {"system": "PORTAL", "env": "prod"}


def test_a_cancel_by_someone_who_may_is_audited(monkeypatch):
    seen = stub(monkeypatch, allowed=True)
    assert run_actions.cancel_run("r1") is True
    assert seen["cancelled"] == ["r1"] and seen["audit"] == [(("run.cancel", "r1"), {})]


def test_a_run_that_is_not_there_or_not_running_is_no_cancel(monkeypatch):
    seen = stub(monkeypatch, allowed=True)
    assert run_actions.cancel_run("nope") is False and run_actions.cancel_run("") is False
    seen = stub(monkeypatch, allowed=True, cancels=False)
    assert run_actions.cancel_run("r1") is False and seen["audit"] == []
