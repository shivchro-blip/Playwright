"""Quarantine a flaky test case (it still runs, its failures stop failing the run) or release it."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.state import services
from webapp.ui.components import text


async def toggle_quarantine(test_id: str, after: Callable[[], None] | None = None) -> None:
    svc = services()
    if not test_id or not guard("edit", "quarantine a test case", test_id):
        return
    if svc.quarantine.is_quarantined(test_id):
        svc.quarantine.remove(test_id)
        audit("quarantine.release", test_id)
        ui.notify(f"{test_id} is released: its failures fail runs again.", type="positive")
    else:
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[28rem]"):
            text("h2", f"Quarantine {test_id}", "any-title")
            ui.label("It keeps running, but when it fails the run is not failed: the result shows as xfailed with "
                     "your reason. Release it once it is fixed.").classes("any-muted text-sm")
            reason = ui.input(placeholder="Why (for example: fails 1 run in 4 on the date picker)") \
                .props('aria-label="Reason" data-testid="quarantine-reason"').classes("w-full")
            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38")
                ui.button("Quarantine", color=None, on_click=lambda: dialog.submit(True)) \
                    .props('data-testid="quarantine-ok"').classes("any-primary")
        if not await dialog:
            dialog.delete()
            return
        dialog.delete()
        svc.quarantine.add(test_id, reason.value or "")
        audit("quarantine.add", test_id, reason=reason.value or "")
        ui.notify(f"{test_id} is quarantined.", type="info")
    if after:
        after()
