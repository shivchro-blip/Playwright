"""Clear finished runs (their console logs, results, reports, screenshots and traces): this run, or all of them."""
from __future__ import annotations

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.run_manager import Run
from webapp.state import services
from webapp.ui.components import text
from webapp.ui.theme import icon


async def clear_runs(run: Run | None) -> list[str]:
    """Ask what to clear (the shown run, or every finished run) and do it. Returns the IDs that were deleted;
    nothing happens if the person cancels or may not delete. Runs in progress or waiting are never touched."""
    svc = services()
    if not guard("delete", "clear runs", run.id if run else ""):
        return []
    finished = [r for r in svc.runs.list() if not r.active]
    if not finished:
        ui.notify("There are no finished runs to clear.", type="info")
        return []
    can_one = bool(run and not run.active)
    choice = {"scope": "one" if can_one else "all"}
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[34rem]"):
        text("h2", "Clear runs", "any-title")
        ui.label("Deletes the console log, results, report, screenshots and traces of the runs you choose, so the "
                 "next run starts on a clean page. This cannot be undone.").classes("any-muted text-sm")
        options = {"all": f"All {len(finished)} finished run{'' if len(finished) == 1 else 's'}"}
        if can_one:
            options = {"one": f"Only this run ({run.id})", **options}
        ui.radio(options, value=choice["scope"], on_change=lambda e: choice.update(scope=e.value)) \
            .props('data-testid="clear-scope"')
        ui.label("Runs in progress or waiting in the queue are never deleted.").classes("any-muted text-xs")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38")
            ui.button("Delete", icon=icon("trash-2"), color=None, on_click=lambda: dialog.submit(True)) \
                .props('data-testid="clear-confirm"').classes("any-danger")
    dialog.open()
    if not await dialog:
        dialog.delete()
        return []
    dialog.delete()
    ids = [run.id] if choice["scope"] == "one" and run else [r.id for r in finished]
    deleted = [i for i in ids if svc.runs.delete(i)]
    audit("run.clear", ", ".join(deleted[:5]) + (" ..." if len(deleted) > 5 else ""), count=len(deleted))
    ui.notify(f"Cleared {len(deleted)} run{'' if len(deleted) == 1 else 's'}", type="positive")
    return deleted
