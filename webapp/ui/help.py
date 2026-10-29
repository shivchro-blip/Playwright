"""Help tab: topic list grouped by category, full-text search, Markdown viewer."""
from __future__ import annotations

from itertools import groupby

from nicegui import ui

from webapp.services.help_service import HelpService, HelpTopic
from webapp.state import services
from webapp.ui.components import text


class HelpPanel:
    def __init__(self):
        self.help: HelpService = services().help
        topics = self.help.topics()
        self.current: HelpTopic | None = topics[0] if topics else None
        self.query = ""
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("div").classes("any-split"):
                with ui.element("nav").classes("any-card any-split-list any-help-nav") \
                        .props('aria-label="Help topics"'):
                    with ui.element("div").classes("any-list-head"):
                        ui.input(placeholder="Search help", on_change=self.on_search) \
                            .props('clearable debounce=250 aria-label="Search help"').classes("w-full")
                    self.nav()
                with ui.element("article").classes("any-card any-split-main any-help-body"):
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
        with ui.element("div").classes("any-help-list"):
            if self.query.strip():
                hits = self.help.search(self.query)
                ui.label(f"{len(hits)} result(s)").classes("any-eyebrow px-2")
                for hit in hits:
                    with ui.element("button").classes("any-help-item is-hit").props('type="button"') \
                            .on("click", lambda t=hit.topic: self.show(t)):
                        text("span", hit.topic.title, "any-help-title")
                        text("span", hit.snippet, "any-help-snippet")
                return
            topics = self.help.topics()
            if not topics:
                ui.label("No help topics found (docs/help/help.index.json).").classes("any-err-line px-2")
            for category, items in groupby(topics, key=lambda t: t.category):
                text("div", category, "any-eyebrow any-help-cat")
                for topic in items:
                    active = self.current is not None and topic.id == self.current.id
                    item = text("button", topic.title, "any-help-item" + (" is-active" if active else "")) \
                        .props('type="button"' + (' aria-current="page"' if active else ""))
                    item.on("click", lambda t=topic: self.show(t))

    @ui.refreshable_method
    def content(self) -> None:
        if not self.current:
            ui.label("Select a topic.").classes("any-muted")
            return
        ui.markdown(self.help.markdown(self.current), extras=["tables", "fenced-code-blocks"]) \
            .classes("w-full help-doc")
