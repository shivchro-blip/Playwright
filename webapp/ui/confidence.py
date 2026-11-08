"""Confidence tab: per environment, is it safe to release and is it working (webapp/services/confidence.py), and
every journey's status on every environment. The Journeys, Releases, Monitors and Impact tabs are a click away."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.services.confidence import build_for
from webapp.state import services
from webapp.ui.components import field, text
from webapp.ui.live import live
from webapp.ui.theme import icon

VERDICT_TONE = {"blocked": "failed", "at_risk": "flaky", "unknown": "idle", "ready": "passed"}
VERDICT_ICON = {"blocked": "circle-x", "at_risk": "triangle-alert", "unknown": "circle-help", "ready": "circle-check"}
STATE_TONE = {"failing": "failed", "incomplete": "flaky", "stale": "idle", "not_run": "idle", "passing": "passed"}
MONITOR_TONE = {"up": "passed", "down": "failed", "unknown": "idle", "never": "idle"}


class ConfidencePanel:
    def __init__(self, go: Callable[[str], None] | None = None):
        self.svc = services()
        self.go = go or (lambda name: None)
        systems = self.svc.systems.systems()
        default = self.svc.env.load().get("systemConfig")
        self.system = default if default in systems else (systems[0] if systems else "")
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("div").classes("any-page-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h1", "Confidence", "any-title is-lg")
                    ui.label("Is it safe to release, and is it working? Each environment's answer, from its "
                             "journeys, the latest release decision and its monitors.").classes("any-muted text-sm")
                with ui.element("div").classes("any-head-right"):
                    with field("System", "cf-system"):
                        ui.select(systems, value=self.system or None, on_change=lambda e: self._set_system(e.value)) \
                            .props('for="cf-system" data-testid="confidence-system"').classes("w-44")
            self.body()
        live(self.svc.bus, ["run", "workspace", "monitor"], self.body.refresh,
             when=lambda e: e["topic"] != "run" or e.get("change") in ("finished", "deleted"))

    def _set_system(self, value: str | None) -> None:
        self.system = value or ""
        self.body.refresh()

    @ui.refreshable_method
    def body(self) -> None:
        if not self.system:
            ui.label("No systems in systemData.json yet.").classes("any-muted")
            return
        hub = build_for(self.svc, self.system)
        with ui.element("div").classes("any-cf-envs").props('data-testid="confidence-envs"'):
            for e in hub.envs:
                with ui.element("section").classes(f"any-card any-cf-env is-{e.verdict}").props(
                        f'aria-label="{e.env}" data-env="{e.env}" data-verdict="{e.verdict}"'):
                    with ui.element("div").classes("any-cf-env-head"):
                        text("h2", e.env, "any-title")
                        with ui.element("span").classes(f"any-pill pill-{VERDICT_TONE[e.verdict]}"):
                            ui.icon(icon(VERDICT_ICON[e.verdict])).props('aria-hidden="true"')
                            text("span", e.label)
                    if e.reasons:
                        with ui.element("ul").classes("any-cf-reasons"):
                            for reason in e.reasons:
                                text("li", reason)
                    else:
                        text("div", "Every journey that ran here is passing.", "any-muted text-sm")
                    with ui.element("dl").classes("any-kv"):
                        counts = e.journeys
                        text("dt", "Journeys")
                        text("dd", ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in sorted(counts.items()))
                             or "none")
                        text("dt", "Release")
                        if e.release:
                            text("dd", f"{e.release['name']}: {str(e.release.get('decision') or '').upper()}, "
                                       f"{str(e.release.get('outcome') or '').replace('-', ' ')}")
                        else:
                            text("dd", "no release check yet", "any-muted")
                        text("dt", "Monitor")
                        if e.monitor:
                            text("dd", f"{e.monitor['up']} up, {e.monitor['down']} down"
                                       + (f", {e.monitor['incidents']} incident(s) open"
                                          if e.monitor['incidents'] else ""))
                        else:
                            text("dd", "not monitored", "any-muted")
                    with ui.element("div").classes("any-cf-actions"):
                        ui.button("Release check", icon=icon("shield"), color=None,
                                  on_click=lambda: self.go("Releases")).props('flat dense no-caps').classes("any-btn")
                        ui.button("Monitors", icon=icon("activity"), color=None,
                                  on_click=lambda: self.go("Monitors")).props('flat dense no-caps').classes("any-btn")

        with ui.element("section").classes("any-card").props('aria-label="Journeys by environment"'):
            with ui.element("div").classes("any-card-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h2", "Journeys by environment", "any-title")
                    text("span", str(len(hub.journeys)), "any-count-pill")
                with ui.element("div").classes("any-head-right"):
                    ui.button("Open Journeys", icon=icon("circle-check"), color=None,
                              on_click=lambda: self.go("Journeys")).props('flat no-caps').classes("any-btn")
                    ui.button("What a change affects", icon=icon("git-compare"), color=None,
                              on_click=lambda: self.go("Impact")).props('flat no-caps').classes("any-btn")
            if not hub.journeys:
                ui.label("No journeys for this system yet: create them on the Journeys tab.") \
                    .classes("any-muted p-4")
                return
            envs = [e.env for e in hub.envs]
            with ui.element("div").classes("any-table-wrap"):
                with ui.element("table").classes("any-cf-matrix").props('data-testid="confidence-matrix"'):
                    with ui.element("thead"), ui.element("tr"):
                        text("th", "Journey")
                        for env in envs:
                            text("th", env)
                    with ui.element("tbody"):
                        for row in hub.journeys:
                            with ui.element("tr").props(f'data-journey="{row.id}"'):
                                with ui.element("td"):
                                    text("div", row.name, "any-cf-name")
                                    text("div", " · ".join(x for x in (row.criticality, row.owner) if x),
                                         "any-muted text-xs")
                                for env in envs:
                                    st = row.statuses.get(env) or {}
                                    with ui.element("td").props(f'data-env="{env}"'):
                                        state = st.get("state", "not_run")
                                        text("span", st.get("label", "never run"),
                                             f"any-pill pill-{STATE_TONE.get(state, 'idle')}")
                                        if row.monitored.get(env):
                                            m = row.monitored[env]
                                            text("span", f"monitor: {m}", f"any-tag is-{MONITOR_TONE.get(m)}")
                                        if st.get("where"):
                                            text("div", st["where"], "any-muted text-xs mono")
