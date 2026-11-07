"""The confidence hub: one verdict per environment, with its reasons, from journeys, releases and monitors."""
from types import SimpleNamespace

from webapp.services.confidence import build
from webapp.services.journeys import JourneyStatus


def overview(jid, name, criticality, **states):
    statuses = {env: JourneyStatus(env, state, where="Payment > pay" if state == "failing" else "",
                                   last_at=f"2026-11-0{i + 1}T10:00:00")
                for i, (env, state) in enumerate(states.items())}
    journey = SimpleNamespace(id=jid, name=name, criticality=criticality, owner="ann")
    return SimpleNamespace(journey=journey, statuses=statuses)


def monitor(env, *journey_states, incidents=()):
    m = SimpleNamespace(id=f"m-{env}", name=f"{env} watch", system="S", env=env)
    return m, {"journeys": [{"id": jid, "name": jid.title(), "state": st} for jid, st in journey_states],
               "incidents": list(incidents), "last_check": "2026-11-07T10:00:00"}


def verdicts(hub):
    return {e.env: (e.verdict, e.reasons) for e in hub.envs}


def test_each_environment_gets_one_verdict_with_its_reasons():
    hub = build("S", ["dev", "uat", "prod", "perf"], [
        overview("renew", "Renew a licence", "critical", dev="passing", uat="failing", prod="passing",
                 perf="not_run"),
        overview("search", "Search", "normal", dev="failing", uat="passing", prod="passing", perf="not_run"),
        overview("help", "Help page", "high", dev="passing", uat="passing", prod="stale", perf="not_run"),
    ])
    got = verdicts(hub)
    assert got["uat"] == ("blocked", ["Renew a licence is failing (Payment > pay) — critical"])
    assert got["dev"] == ("at_risk", ["Search is failing (Payment > pay)"])
    assert got["prod"] == ("at_risk", ["Help page has not run recently (high)"])
    assert got["perf"] == ("unknown", ["no journey has run here yet"])
    assert [j.name for j in hub.journeys] == ["Renew a licence", "Help page", "Search"]  # most critical first
    assert hub.envs[1].journeys == {"failing": 1, "passing": 2}


def test_releases_and_monitors_join_the_verdict():
    journeys = [overview("renew", "Renew a licence", "critical", uat="passing", prod="passing")]
    releases = [{"id": "r1", "name": "Build 481", "env": "uat", "decision": "go", "outcome": "approved",
                 "created_at": "2026-11-01T09:00:00"},
                {"id": "r2", "name": "Build 482", "env": "uat", "decision": "no-go", "outcome": "awaiting",
                 "created_at": "2026-11-02T09:00:00"},
                {"id": "r3", "name": "Build 480", "env": "prod", "decision": "go", "outcome": "rejected",
                 "created_at": "2026-11-01T09:00:00"}]
    got = verdicts(build("S", ["uat", "prod"], journeys, releases))
    assert got["uat"] == ("at_risk", ["the latest release, Build 482, is NO-GO and not signed off"])
    assert got["prod"] == ("blocked", ["the latest release, Build 480, was rejected"])

    hub = build("S", ["uat", "prod"], journeys, [], [monitor("prod", ("renew", "down")),
                                                     monitor("uat", ("renew", "up"), incidents=[{"ended": ""}])])
    got = verdicts(hub)
    assert got["prod"] == ("blocked", ["monitored and down: Renew"])
    assert got["uat"] == ("at_risk", ["1 incident open"])
    assert hub.envs[0].monitor["up"] == 1 and hub.journeys[0].monitored == {"prod": "down", "uat": "up"}


def test_ready_when_everything_that_ran_passes():
    hub = build("S", ["uat"], [overview("renew", "Renew", "critical", uat="passing"),
                               overview("new", "New thing", "normal", uat="not_run")],
                [{"id": "r", "name": "B", "env": "uat", "decision": "go", "outcome": "approved",
                  "created_at": "x"}], [monitor("uat", ("renew", "up"))])
    env = hub.envs[0]
    assert (env.verdict, env.reasons, env.label) == ("ready", [], "Ready")
    assert env.release["name"] == "B" and hub.to_dict()["envs"][0]["label"] == "Ready"
