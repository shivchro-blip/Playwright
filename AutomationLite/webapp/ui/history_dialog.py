"""Versions of one workspace file (page, test file, data set, common actions): what changed, and put one back."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services import history
from webapp.services.json_files import ConflictError, read_json, write_json
from webapp.ui.components import icon_button, text


def _current_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def open_file_history(title: str, path: Path, reload: Callable[[], Awaitable[None] | None]) -> None:
    """`reload` re-opens the file in its editor after a restore."""
    found = history.versions(path)
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Edit history"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "Edit history", "any-eyebrow")
                text("h2", title, "any-title")
            icon_button("x", "Close", dialog.close)
        if not found:
            ui.label("No earlier versions yet. Each time you save, the version it replaces is kept here "
                     f"(the last {history.KEEP}).").classes("any-muted").props('data-testid="history-empty"')
            dialog.open()
            return
        ui.label(f"The last {history.KEEP} versions are kept. Pick one to see what differs from the file now; "
                 "restoring keeps the current file as a version too, so it can be undone.") \
            .classes("any-muted text-sm")
        state = {"id": found[0].id}

        with ui.element("div").classes("any-form-row"):
            picker = ui.select({v.id: f"{v.at}  ({v.size:,} bytes)" for v in found}, value=found[0].id,
                               label="Version", on_change=lambda e: (state.update(id=e.value), body.refresh())) \
                .props('data-testid="history-version"').classes("is-grow")

        @ui.refreshable
        def body() -> None:
            try:
                old = history.read_version(path, state["id"])
            except ValueError as e:
                ui.label(str(e)).classes("any-muted")
                return
            lines = history.diff(old, _current_text(path), f"{path.name} ({picker.options.get(state['id'], '')[:19]})",
                                 f"{path.name} (now)")
            with ui.element("div").classes("any-diff").props('data-testid="history-diff"'):
                if not lines:
                    ui.label("Identical to the file now.").classes("any-muted text-sm")
                for line in lines:
                    kind = ("add" if line.startswith("+") and not line.startswith("+++")
                            else "del" if line.startswith("-") and not line.startswith("---")
                            else "hunk" if line.startswith("@@") else "")
                    text("div", line or " ", f"any-diff-line {('is-' + kind) if kind else ''}")

        body()
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Close", color=None, on_click=dialog.close).classes("any-btn is-38")
            ui.button("Restore this version", color=None, on_click=lambda: restore()) \
                .props('data-testid="history-restore"').classes("any-primary")

        async def restore() -> None:
            if not guard("edit", "restore a version", path.name):
                return
            try:
                data = json.loads(history.read_version(path, state["id"]))
                _, sha, _ = read_json(path)
                write_json(path, data, sha)
            except (ValueError, OSError, ConflictError) as e:
                ui.notify(f"Not restored: {e}", type="negative", multi_line=True)
                return
            audit("history.restore", path.name, version=state["id"])
            ui.notify(f"Restored the version from {picker.options.get(state['id'], '')[:19]}", type="positive")
            dialog.close()
            result = reload()
            if result is not None:
                await result
    dialog.open()
