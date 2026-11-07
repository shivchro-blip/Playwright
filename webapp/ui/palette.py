"""The command palette: Ctrl+K (Cmd+K on a Mac), or the search button in the header, opens one box that finds
any section, action, test case, page, field, journey or recent run and goes there (webapp/services/commands.py).

Up and Down move through the results, Enter opens the highlighted one, Esc closes. What "opens" means is the
shell's business (`run_command`), so the palette itself only finds things.
"""
from __future__ import annotations

import inspect
import logging
from typing import Callable

from nicegui import ui

from webapp.services.commands import Command, build, rank
from webapp.state import services
from webapp.ui.components import text
from webapp.ui.theme import icon

logger = logging.getLogger(__name__)

EVENT = "autoplay-palette"
HASH_EVENT = "autoplay-hash"  # the address's #section changed (a link, Back or Forward)
# Ctrl+K / Cmd+K anywhere on the page, even in a text box; the browser's own Ctrl+K (search bar) is kept from it
SHORTCUT_JS = f"""
<script>
window.__autoplayStartHash = location.hash;  // the section asked for, before the app touches the address
document.addEventListener('keydown', function (e) {{
  if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === 'k' || e.key === 'K')) {{
    e.preventDefault();
    if (window.emitEvent) emitEvent('{EVENT}');
  }}
}});
window.addEventListener('hashchange', function () {{
  if (window.emitEvent) emitEvent('{HASH_EVENT}', location.hash);
}});
</script>
"""
LIMIT = 20
KIND_LABEL = {"tab": "Section", "action": "Action", "test": "Test case", "journey": "Journey", "page": "Page",
              "field": "Field", "run": "Run"}


class CommandPalette:
    def __init__(self, run_command: Callable[[Command], object]):
        self.svc = services()
        self.run_command = run_command
        self.commands: list[Command] = []
        self.results: list[Command] = []
        self.active = 0
        with ui.dialog().props('position="top"') as self.dialog, \
                ui.card().classes("any-app any-palette").props('role="dialog" aria-label="Command palette"'):
            with ui.element("div").classes("any-palette-head"):
                ui.icon(icon("search")).classes("any-palette-icon").props('aria-hidden="true"')
                self.input = ui.input(placeholder="Search tests, pages, fields, journeys, runs and sections") \
                    .props('borderless dense autofocus aria-label="Search everything" data-testid="palette-input" '
                           'aria-controls="palette-results" aria-autocomplete="list"') \
                    .classes("any-palette-input")
                text("kbd", "Esc", "any-kbd")
            self.input.on_value_change(lambda _: self._search())
            self.input.on("keydown.down.prevent", lambda: self._move(1))
            self.input.on("keydown.up.prevent", lambda: self._move(-1))
            self.input.on("keydown.enter.prevent", self._choose_active)
            self.list_view()
            with ui.element("div").classes("any-palette-foot"):
                text("span", "↑ ↓ to move · Enter to open · Esc to close", "any-muted")
        ui.on(EVENT, self.open)

    def open(self) -> None:
        if self.dialog.value:
            self.dialog.close()
            return
        self.commands = self._commands()
        self.input.value = ""
        self._search()
        self.dialog.open()
        self.input.run_method("focus")

    def _commands(self) -> list[Command]:
        try:
            from webapp.services.workspace_model import model_for
            snapshot = model_for(self.svc.settings.workspace, self.svc.tests).snapshot()
        except Exception:  # noqa: BLE001 - a broken workspace file must not take the palette down
            logger.exception("Command palette: workspace model unavailable")
            snapshot = None
        return build(snapshot, self.svc.runs.list())

    def _search(self) -> None:
        self.results = rank(self.input.value or "", self.commands, LIMIT)
        self.active = 0
        self.list_view.refresh()

    def _move(self, delta: int) -> None:
        if self.results:
            self.active = (self.active + delta) % len(self.results)
            self.list_view.refresh()

    async def _choose_active(self) -> None:
        if self.results:
            await self._choose(self.results[self.active])

    async def _choose(self, command: Command) -> None:
        self.dialog.close()
        result = self.run_command(command)
        if inspect.isawaitable(result):
            await result

    @ui.refreshable_method
    def list_view(self) -> None:
        with ui.element("ul").classes("any-palette-list").props(
                'id="palette-results" role="listbox" aria-label="Results" data-testid="palette-results"'):
            if not self.results:
                with ui.element("li").classes("any-palette-empty"):
                    ui.label("Nothing matches. Try fewer letters, or a test ID, page or field name.")
                return
            for i, command in enumerate(self.results):
                selected = i == self.active
                item = ui.element("li").classes("any-palette-item" + (" is-active" if selected else "")).props(
                    f'role="option" aria-selected="{str(selected).lower()}" data-kind="{command.kind}"')
                item.on("click", lambda c=command: self._choose(c))
                with item:
                    ui.icon(icon(command.icon or "search")).classes("any-palette-item-icon") \
                        .props('aria-hidden="true"')
                    with ui.element("div").classes("any-palette-item-text"):
                        text("div", command.title, "any-palette-title")
                        if command.subtitle:
                            text("div", command.subtitle, "any-palette-sub")
                    text("span", KIND_LABEL.get(command.kind, ""), "any-palette-kind")
