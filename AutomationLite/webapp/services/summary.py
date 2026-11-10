"""One instance's health in one call, for the control plane: journeys, releases, monitors, runs, runners.

Kept small and cheap: counts and the few items someone looking across many teams needs to act on (failing
critical journeys, the latest release decision, monitors that are down).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

SUMMARY_VERSION = 1


def _ago(stamp: str, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(stamp[:19])).total_seconds()
    except (TypeError, ValueError):
        return None


def build_summary(svc, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    systems = svc.systems.systems()
    journeys: dict[str, dict] = {}
    attention: list[dict] = []
    total = Counter()
    for system in systems:
        per_env: dict[str, Counter] = {}
        for o in svc.journeys.overview(system):
            for env, st in o.statuses.items():
                per_env.setdefault(env, Counter())[st.state] += 1
                total[st.state] += 1
                if st.state == "failing" and o.journey.criticality in ("critical", "high"):
                    attention.append({"system": system, "env": env, "journey": o.journey.name,
                                      "criticality": o.journey.criticality, "where": st.where})
        journeys[system] = {env: dict(c) for env, c in per_env.items()}
    attention.sort(key=lambda a: (a["criticality"] != "critical", a["system"], a["journey"]))

    releases = [svc.releases.summary(p) for p in svc.releases.store.list()[:5]]
    latest = [{k: r.get(k) for k in ("id", "name", "system", "env", "created_at", "decision", "outcome", "valid")}
              for r in releases]

    monitors = []
    for m in svc.monitors.store.list():
        s = svc.monitors.summary(m)
        states = Counter(j["state"] for j in s["journeys"])
        monitors.append({"id": m.id, "name": m.name, "system": m.system, "env": m.env, "enabled": m.enabled,
                         "up": states["up"], "down": states["down"], "unknown": states["unknown"],
                         "open_incidents": sum(1 for i in s["incidents"] if not i["ended"]),
                         "down_journeys": [j["name"] for j in s["journeys"] if j["state"] == "down"],
                         "last_check": s["last_check"]})

    runs = svc.runs.list()
    day = [r for r in runs if r.finished_at and (_ago(r.finished_at, now) or 0) <= 86400]
    last = next((r for r in runs if r.finished_at), None)
    runners = svc.runners.list()
    return {
        "app": "autoplay", "summary_version": SUMMARY_VERSION, "at": now.isoformat(timespec="seconds"),
        "public_url": svc.settings.resolved_public_url, "systems": systems,
        "journeys": {"by_system": journeys, "total": dict(total), "attention": attention[:20]},
        "releases": latest,
        "monitors": monitors,
        "runs": {"active": sum(1 for r in runs if r.status == "running"),
                 "queued": sum(1 for r in runs if r.status == "queued"),
                 "last_24h": dict(Counter(r.status for r in day)),
                 "last": {"id": last.id, "status": last.status, "system": last.config.system,
                          "env": last.config.env, "finished_at": last.finished_at} if last else None},
        "runners": {"total": len(runners), "online": sum(1 for r in runners if r["online"])},
    }


def health(summary: dict | None) -> tuple[str, list[str]]:
    """red | amber | green | unknown, with the reasons: red when a critical or high journey is failing or a
    monitor is down; amber when another journey is failing or the latest release is NO-GO."""
    if not summary:
        return "unknown", ["not reachable"]
    red, amber = [], []
    for a in summary["journeys"]["attention"]:
        red.append(f"{a['journey']} failing on {a['system']}/{a['env']} ({a['criticality']})")
    for m in summary["monitors"]:
        if m["down"]:
            red.append(f"monitor {m['name']}: {', '.join(m['down_journeys']) or m['down']} down")
    failing = summary["journeys"]["total"].get("failing", 0) - len(summary["journeys"]["attention"])
    if failing > 0:
        amber.append(f"{failing} other journey status{'es' if failing != 1 else ''} failing")
    if summary["releases"] and summary["releases"][0].get("decision") == "NO-GO":
        r = summary["releases"][0]
        amber.append(f"latest release {r['name']} is NO-GO")
    if red:
        return "red", red + amber
    if amber:
        return "amber", amber
    return "green", []


