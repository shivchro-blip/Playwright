"""Page shell: header with the main tabs. Each tab's content lives in its own module."""
from __future__ import annotations

from nicegui import ui

from webapp.ui.control import ControlPanel
from webapp.ui.generator import GeneratorPanel
from webapp.ui.help import HelpPanel
from webapp.ui.history import HistoryPanel

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
    @ui.page("/")
    def index() -> None:
        ui.page_title("AutomateNY")
        ui.add_css(CSS)
        with ui.header().classes("items-center"):
            ui.icon("smart_toy").classes("text-2xl")
            ui.label("AutomateNY").classes("text-xl font-medium")
            with ui.tabs().props("inline-label") as tabs:
                control_tab = ui.tab("Control", icon="play_circle")
                generator_tab = ui.tab("Generator", icon="edit_note")
                history_tab = ui.tab("History", icon="history")
                help_tab = ui.tab("Help", icon="help")
            ui.space()
            ui.link("API", "/docs", new_tab=True).classes("text-white text-sm")

        panels: dict[str, object] = {}

        def watch(run_id: str) -> None:
            """Show a run live on the Control tab (used by Generator 'Run' and History 'Re-run')."""
            tabs.set_value(control_tab)
            panels["control"].view_run(run_id)
            panels["control"].history.refresh()

        with ui.tab_panels(tabs, value=control_tab).classes("w-full bg-transparent"):
            with ui.tab_panel(control_tab).classes("p-0"):
                panels["control"] = ControlPanel()
            with ui.tab_panel(generator_tab).classes("p-0"):
                GeneratorPanel(on_run=watch)
            with ui.tab_panel(history_tab).classes("p-0"):
                HistoryPanel(on_watch=watch)
            with ui.tab_panel(help_tab).classes("p-0"):
                HelpPanel()
