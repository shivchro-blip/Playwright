"""Live console element: colours pytest/framework log lines client-side, sends only new lines per tick."""
from __future__ import annotations

from nicegui.element import Element


class ConsoleView(Element, component="console_view.js"):

    def __init__(self, max_lines: int, lines: list[str] | None = None) -> None:
        """lines: initial content (sent with the element itself, e.g. when built inside a refresh)."""
        super().__init__()
        self.max_lines = max_lines
        self._props["lines"] = list(lines or [])[-max_lines:]
        self._props["maxLines"] = max_lines
        self._props["active"] = False

    def _connected(self) -> bool:
        return self.client.has_socket_connection

    def push(self, lines: list[str]) -> None:
        """Append lines. The server copy is what a (re)render shows; live clients get only the new lines."""
        buffer: list[str] = self._props["lines"]
        buffer.extend(lines)
        del buffer[:-self.max_lines]
        if self._connected():
            self.run_method("push", lines)

    def clear(self) -> None:
        self._props["lines"] = []
        if self._connected():
            self.run_method("clear")

    def set_active(self, value: bool) -> None:
        if self._props["active"] == value:
            return
        self._props["active"] = value
        if self._connected():
            self.run_method("setActive", value)
