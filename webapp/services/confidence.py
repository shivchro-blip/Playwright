"""The confidence hub: for each environment of a system, one answer to "is it safe to release?" and "is it working?".

Journeys are the unit: a business transaction and the test cases that prove it (webapp/services/journeys.py). Their
status on each environment, the latest release decision for it (webapp/services/releases.py) and what its monitors
see (webapp/services/monitors.py) are read together into one verdict per environment, with the reasons:

  blocked  a critical or high journey is failing, a monitored journey is down, or the latest release was rejected
  at risk  another journey is failing or only partly run, a critical journey has not run recently, an incident is
           open, or the latest release decision is NO-GO and not yet signed off
  ready    every journey that has run is passing, and nothing above applies
  unknown  nothing has run there yet

Pure: it takes what the services already compute.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field

VERDICTS = ("blocked", "at_risk", "unknown", "ready")  # worst first
VERDICT_LABELS = {"blocked": "Blocked", "at_risk": "At risk", "unknown": "Not known yet", "ready": "Ready"}
SERIOUS = ("critical", "high")


@dataclass
class EnvConfidence:
    env: str
    verdict: str = "unknown"
    reasons: list[str] = field(default_factory=list)
    journeys: dict = field(default_factory=dict)  # state -> count
    release: dict | None = None  # the latest release pack for this env: id, name, decision, outcome, created_at
    monitor: dict | None = None  # name, up, down, unknown, incidents (open), last_check
    last_at: str = ""  # the latest journey result here

    @property
    def label(self) -> str:
        return VERDICT_LABELS[self.verdict]


@dataclass
class JourneyRow:
    id: str
    name: str
    criticality: str
    owner: str
    statuses: dict = field(default_factory=dict)  # env -> {state, label, where, pass_rate, last_at, last_run}
    monitored: dict = field(default_factory=dict)  # env -> up | down | unknown | never


@dataclass
class Hub:
    system: str
    envs: list[EnvConfidence] = field(default_factory=list)
    journeys: list[JourneyRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"system": self.system,
                "envs": [{**asdict(e), "label": e.label} for e in self.envs],
                "journeys": [asdict(j) for j in self.journeys]}


def _latest_release(releases: list[dict], env: str) -> dict | None:
    mine = sorted((r for r in releases if r.get("env") == env), key=lambda r: str(r.get("created_at", "")),
                  reverse=True)
    if not mine:
        return None
    r = mine[0]
    return {k: r.get(k) for k in ("id", "name", "decision", "outcome", "created_at")}


def build(system: str, envs: list[str], overviews, releases: list[dict] = (),
          monitors: list[tuple] = ()) -> Hub:
    """overviews: JourneyService.overview(system); releases: ReleaseService.summary(...) of the system's packs;
    monitors: (Monitor, MonitorService.summary(monitor)) of the system's monitors."""
    hub = Hub(system)
    by_env_monitor: dict[str, list[tuple]] = {}
    for monitor, summary in monitors:
        if monitor.system == system:
            by_env_monitor.setdefault(monitor.env, []).append((monitor, summary))
    for o in overviews:
        row = JourneyRow(o.journey.id, o.journey.name, o.journey.criticality, o.journey.owner)
        for env, st in o.statuses.items():
            row.statuses[env] = {"state": st.state, "label": st.label, "where": st.where,
                                 "pass_rate": st.pass_rate, "last_at": st.last_at, "last_run": st.last_run}
        for env, items in by_env_monitor.items():
            for _, summary in items:
                for j in summary.get("journeys") or []:
                    if j.get("id") == o.journey.id:
                        row.monitored[env] = j.get("state", "never")
        hub.journeys.append(row)
    hub.journeys.sort(key=lambda r: (("critical", "high", "normal").index(r.criticality)
                                     if r.criticality in ("critical", "high", "normal") else 3, r.name.casefold()))

    for env in envs:
        e = EnvConfidence(env)
        counts: Counter = Counter()
        blocked, risk = [], []
        for row in hub.journeys:
            st = row.statuses.get(env)
            if not st:
                continue
            counts[st["state"]] += 1
            e.last_at = max(e.last_at, st.get("last_at") or "")
            where = f" ({st['where']})" if st.get("where") else ""
            if st["state"] == "failing":
                (blocked if row.criticality in SERIOUS else risk).append(
                    f"{row.name} is failing{where}" + (f" — {row.criticality}" if row.criticality in SERIOUS else ""))
            elif st["state"] == "incomplete":
                risk.append(f"{row.name} only partly ran")
            elif st["state"] == "stale" and row.criticality in SERIOUS:
                risk.append(f"{row.name} has not run recently ({row.criticality})")
        e.journeys = dict(counts)
        e.release = _latest_release(list(releases), env)
        if e.release:
            if e.release.get("outcome") == "rejected":
                blocked.append(f"the latest release, {e.release.get('name')}, was rejected")
            elif e.release.get("decision") != "go" and e.release.get("outcome") == "awaiting":
                risk.append(f"the latest release, {e.release.get('name')}, is NO-GO and not signed off")
        items = by_env_monitor.get(env) or []
        if items:
            states: Counter = Counter()
            open_incidents, last_check, down = 0, "", []
            for monitor, summary in items:
                for j in summary.get("journeys") or []:
                    states[j.get("state", "never")] += 1
                    if j.get("state") == "down":
                        down.append(j.get("name", j.get("id", "")))
                open_incidents += sum(1 for i in summary.get("incidents") or [] if not i.get("ended"))
                last_check = max(last_check, summary.get("last_check") or "")
            e.monitor = {"name": ", ".join(m.name for m, _ in items), "up": states["up"], "down": states["down"],
                         "unknown": states["unknown"] + states["never"], "incidents": open_incidents,
                         "last_check": last_check}
            if down:
                blocked.append(f"monitored and down: {', '.join(down)}")
            elif open_incidents:
                risk.append(f"{open_incidents} incident{'s' if open_incidents != 1 else ''} open")
        ran = sum(n for state, n in counts.items() if state != "not_run")
        if blocked:
            e.verdict = "blocked"
        elif risk:
            e.verdict = "at_risk"
        elif not ran and not (e.monitor and e.monitor["up"]):
            e.verdict = "unknown"
            risk.append("no journey has run here yet")
        else:
            e.verdict = "ready"
        e.reasons = blocked + risk
        hub.envs.append(e)
    return hub


def build_for(svc, system: str) -> Hub:
    """The hub of one system from the app's services."""
    envs = svc.systems.environments(system)
    releases = [svc.releases.summary(p) for p in svc.releases.store.list() if p.get("system") == system]
    monitors = [(m, svc.monitors.summary(m)) for m in svc.monitors.store.list() if m.system == system]
    return build(system, envs, svc.journeys.overview(system, envs), releases, monitors)
