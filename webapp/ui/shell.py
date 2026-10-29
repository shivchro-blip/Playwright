"""Page shell: header with the main tabs. Each tab's content lives in its own module."""
from __future__ import annotations

from nicegui import ui

from webapp.ui.control import ControlPanel
from webapp.ui.generator import GeneratorPanel
from webapp.ui.help import HelpPanel
from webapp.ui.history import HistoryPanel
from webapp.ui.theme import add_head, icon, register_assets

CSS = """
.help-doc h1 { font-size: 1.75rem; line-height: 2.2rem; font-weight: 500; margin: 0 0 0.75rem; }
.help-doc h2 { font-size: 1.3rem; line-height: 1.8rem; font-weight: 500; margin: 1.5rem 0 0.5rem; }
.help-doc h3 { font-size: 1.05rem; font-weight: 600; margin: 1.1rem 0 0.4rem; }
.help-doc p, .help-doc li { line-height: 1.55; }
.help-doc table { border-collapse: collapse; margin: 0.5rem 0; }
.help-doc th, .help-doc td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; vertical-align: top; }
.help-doc th { background: #f5f5f5; }
.help-doc pre { background: #f6f8fa; padding: 8px 12px; border-radius: 4px; overflow-x: auto; }
.help-doc code { background: #f2f2f2; padding: 0 3px; border-radius: 3px; }
.help-doc pre code { background: transparent; padding: 0; }
"""


def register() -> None:
    register_assets()

    @ui.page("/")
    def index() -> None:
        ui.page_title("AutomateNY")
        add_head()
        ui.add_css(CSS)
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
