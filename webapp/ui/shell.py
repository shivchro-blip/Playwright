"""Page shell: header with the main tabs. Each tab's content lives in its own module."""
from __future__ import annotations

from nicegui import ui

from webapp.ui.control import ControlPanel
from webapp.ui.generator import GeneratorPanel
from webapp.ui.help import HelpPanel
from webapp.ui.history import HistoryPanel
from webapp.ui.theme import add_head, apply_brand, icon, register_assets



def register() -> None:
    register_assets()

    @ui.page("/")
    def index() -> None:
        ui.page_title("AutomateNY")
        add_head()
        apply_brand()
        # Panels own their padding (the Control tab is full-bleed)
        ui.query(".nicegui-content").classes("any-content")
        tab_defs = [("Control", "circle-play"), ("Generator", "list-plus"), ("History", "history"),
                    ("Help", "circle-help")]
        with ui.header().classes(replace="any-nav"):
            with ui.element("div").classes("any-brand"):
                with ui.element("div").classes("any-brand-mark"):
                    ui.icon(icon("play-solid")).props('aria-hidden="true"')
                ui.label("AutomateNY")
            with ui.tabs().props('inline-label no-caps indicator-color="transparent" '
                                 'aria-label="Main sections"') as tabs:
                tab_elems = {name: ui.tab(name, icon=icon(ico)) for name, ico in tab_defs}
            with ui.element("a").classes("any-api").props('href="/docs" target="_blank" rel="noopener"'):
                ui.label("API")
                ui.icon(icon("external-link")).props('aria-hidden="true"')
        control_tab, generator_tab, history_tab, help_tab = tab_elems.values()

        def mark_current(name: str) -> None:
            for tab_name, tab in tab_elems.items():
                if tab_name == name:
                    tab.props('aria-current="page"')
                else:
                    tab.props(remove="aria-current")

        tabs.on_value_change(lambda e: mark_current(e.value))
        mark_current("Control")

        panels: dict[str, object] = {}

        def watch(run_id: str) -> None:
            """Show a run live on the Control tab (used by Generator 'Run' and History 'Re-run')."""
            tabs.set_value(control_tab)
            panels["control"].view_run(run_id)
            panels["control"].history.refresh()

        with ui.tab_panels(tabs, value=control_tab).classes("w-full bg-transparent"):
            with ui.tab_panel(control_tab).classes("any-app"):
                panels["control"] = ControlPanel()
            with ui.tab_panel(generator_tab).classes("any-app any-page"):
                GeneratorPanel(on_run=watch)
            with ui.tab_panel(history_tab).classes("any-app any-page"):
                HistoryPanel(on_watch=watch)
            with ui.tab_panel(help_tab).classes("any-app any-page"):
                HelpPanel()
