"""Help tab: topic list grouped by category, full-text search, Markdown viewer."""
from __future__ import annotations

from itertools import groupby

from nicegui import ui

from webapp.services.help_service import HelpService, HelpTopic
from webapp.state import services


class HelpPanel:
    def __init__(self):
        self.help: HelpService = services().help
        topics = self.help.topics()
        self.current: HelpTopic | None = topics[0] if topics else None
        self.query = ""
        with ui.splitter(value=24, limits=(16, 40)).classes("w-full") as splitter:
            with splitter.before:
                with ui.column().classes("w-full pr-3 gap-2"):
                    ui.input(placeholder="Search help", on_change=self.on_search) \
                        .props("dense clearable debounce=250").classes("w-full")
                    self.nav()
            with splitter.after:
                with ui.column().classes("w-full pl-6 max-w-4xl"):
                    self.content()

    def on_search(self, e) -> None:
        self.query = e.value or ""
        self.nav.refresh()

    def show(self, topic: HelpTopic) -> None:
        self.current = topic
        self.nav.refresh()
        self.content.refresh()

    @ui.refreshable_method
    def nav(self) -> None:
        if self.query.strip():
            hits = self.help.search(self.query)
            ui.label(f"{len(hits)} result(s)").classes("text-xs text-grey-7")
            for hit in hits:
                with ui.column().classes("w-full gap-0 cursor-pointer rounded p-2 hover:bg-grey-2") \
                        .on("click", lambda t=hit.topic: self.show(t)):
                    ui.label(hit.topic.title).classes("text-sm font-medium text-primary")
                    ui.label(hit.snippet).classes("text-xs text-grey-8")
            return
        topics = self.help.topics()
        if not topics:
            ui.label("No help topics found (docs/help/help.index.json).").classes("text-sm text-negative")
        for category, items in groupby(topics, key=lambda t: t.category):
            ui.label(category).classes("text-xs uppercase tracking-wide text-grey-7 mt-3")
            for topic in items:
                active = self.current is not None and topic.id == self.current.id
                ui.label(topic.title) \
                    .classes("w-full cursor-pointer rounded px-2 py-1 text-sm hover:bg-grey-2 "
                             + ("bg-blue-1 text-primary font-medium" if active else "")) \
                    .on("click", lambda t=topic: self.show(t))

    @ui.refreshable_method
    def content(self) -> None:
        if not self.current:
            ui.label("Select a topic.").classes("text-grey-7")
            return
        ui.markdown(self.help.markdown(self.current), extras=["tables", "fenced-code-blocks"]) \
            .classes("w-full help-doc")
