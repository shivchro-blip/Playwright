"""Map tab: the system's screens and how the tests move between them, colored by how well each screen is tested
(webapp/services/app_map.py), with the screens that need attention listed first."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.services.app_map import map_for, mermaid
from webapp.state import services
from webapp.ui.components import field, text
from webapp.ui.live import live

STATE_LABEL = {"untested": "untested", "failing": "failing", "new": "new", "tested": "tested"}
STATE_TONE = {"untested": "flaky", "failing": "failed", "new": "idle", "tested": "passed"}


class MapPanel:
    def __init__(self, open_page: Callable[[str, str], object] | None = None):
        self.svc = services()
        self.open_page = open_page
        systems = self.svc.systems.systems()
        default = self.svc.env.load().get("systemConfig")
        self.system = default if default in systems else (systems[0] if systems else "")
        self.only = "all"
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("div").classes("any-page-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h1", "Application map", "any-title is-lg")
                    ui.label("The screens of the application and how the tests move between them. Untested and "
                             "failing screens stand out; a new screen with hardly a test is marked new.") \
                        .classes("any-muted text-sm")
                with ui.element("div").classes("any-head-right"):
                    with field("System", "map-system"):
                        ui.select(systems, value=self.system or None, on_change=lambda e: self._set_system(e.value)) \
                            .props('for="map-system" data-testid="map-system"').classes("w-44")
            self.body()
        live(self.svc.bus, ["run", "workspace"], self.body.refresh,
             when=lambda e: e["topic"] != "run" or e.get("change") in ("finished", "deleted"))

    def _set_system(self, value: str | None) -> None:
        self.system = value or ""
        self.body.refresh()

    def _filter(self, value: str) -> None:
        self.only = value
        self.body.refresh()

    @ui.refreshable_method
    def body(self) -> None:
        if not self.system:
            ui.label("No systems in systemData.json yet.").classes("any-muted")
            return
        try:
            m = map_for(self.svc, self.system)
        except Exception as e:  # noqa: BLE001 - an unreadable workspace: say it, keep the tab
            ui.label(f"The map could not be built: {e}").classes("any-err-line")
            return
        s = m.summary()
        with ui.element("div").classes("any-map-summary").props('data-testid="map-summary"'):
            for key, label in (("screens", "screens"), ("untested", "untested"), ("failing", "failing"),
                               ("new", "new"), ("moves", "moves between screens")):
                with ui.element("div").classes(f"any-map-stat is-{key}"):
                    text("b", str(s[key]))
                    text("span", label)
        if not m.screens:
            ui.label(f"{self.system} has no pages yet.").classes("any-muted p-4")
            return
        with ui.element("section").classes("any-card any-map-graph").props('aria-label="Map"'):
            ui.mermaid(mermaid(m), config={"securityLevel": "strict", "flowchart": {"htmlLabels": True}}) \
                .props('data-testid="map-graph"').classes("w-full")
            with ui.element("div").classes("any-map-legend").props('aria-hidden="true"'):
                for state in ("failing", "untested", "new", "tested"):
                    text("span", STATE_LABEL[state], f"any-map-key is-{state}")
        with ui.element("section").classes("any-card").props('aria-label="Screens" data-testid="map-screens"'):
            with ui.element("div").classes("any-card-head"):
                text("h2", "Screens", "any-title")
                ui.toggle({"all": "All", "attention": "Needs attention"}, value=self.only,
                          on_change=lambda e: self._filter(e.value)) \
                    .props('no-caps unelevated data-testid="map-filter"').classes("any-segmented")
            rows = [x for x in m.screens if self.only == "all" or x.state in ("untested", "failing", "new")]
            with ui.element("table").classes("any-table any-jr-table w-full"):
                with ui.element("thead"):
                    with ui.element("tr"):
                        for head in ("Screen", "State", "Test cases", "Journeys", *m.envs):
                            text("th", head)
                with ui.element("tbody"):
                    for x in rows:
                        with ui.element("tr").props(f'data-screen="{x.name}"'):
                            with ui.element("td"):
                                if self.open_page and x.file:
                                    ui.button(x.name, color=None,
                                              on_click=lambda n=x.name: self.open_page(self.system, n)) \
                                        .props("flat dense no-caps").classes("any-ghost mono") \
                                        .tooltip("Open the page in the Generator")
                                else:
                                    text("span", x.name, "mono")
                                if x.critical:
                                    text("span", "critical", "any-tag is-failed")
                            with ui.element("td"):
                                text("span", STATE_LABEL[x.state], f"any-pill pill-{STATE_TONE[x.state]}")
                            text("td", str(len(x.tests)) + (f" ({x.entry} start here)" if x.entry else ""))
                            text("td", ", ".join(x.journeys) or "—", "any-small")
                            for env in m.envs:
                                c = x.results.get(env)
                                text("td", "—" if not c else f"{c['passed']} passed" + (
                                    f", {c['failed']} failed" if c["failed"] else ""),
                                    "any-small" + (" any-warn-line" if c and c["failed"] else ""))
            if not rows:
                ui.label("Every screen is tested and passing.").classes("any-muted p-4")
