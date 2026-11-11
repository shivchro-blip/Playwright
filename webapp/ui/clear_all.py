"""Clear all: delete everything previous runs left behind (logs, reports, recordings, downloads), after a confirmation
that says what goes and what stays, and a completion message that lists what could not be deleted."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from nicegui import run as nrun
from nicegui import ui

from webapp.auth import audit, guard
from webapp.services import cleanup
from webapp.state import services
from webapp.ui.components import plural, text
from webapp.ui.theme import icon

KEPT = ("Your tests, pages, data sets and input files, settings and .env, saved logins and the secret store, users "
        "and the audit trail, schedules, release packs, and any run that is queued or running.")


def size_text(n: int) -> str:
    value, unit = float(n), "B"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            break
        value /= 1024
    return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"


def _grouped(shared: list[tuple[str, str, int]]) -> list[tuple[str, str, int, int]]:
    """One line per kind of file (a long-used machine has hundreds of context logs): (what, folder, items, bytes)."""
    groups: dict[str, list] = {}
    for what, path, size in shared:
        target = Path(path)
        folder = str(target if target.is_dir() else target.parent)
        entry = groups.setdefault(what, [folder, 0, 0])
        entry[1] += 1
        entry[2] += size
    return [(what, folder, count, size) for what, (folder, count, size) in groups.items()]


async def clear_all(on_done: Callable[[], None] | None = None) -> None:
    """Confirm, delete, report. `on_done` empties what the open pages still show (Control, Generator, History)."""
    svc = services()
    if not guard("delete", "clear all"):
        return
    found = await nrun.io_bound(cleanup.preview, svc.runs, svc.settings.repo_root, svc.settings.workspace)
    if found.empty:
        ui.notify("Nothing to clear: no finished runs or leftover files." + (
            f" {found.active} {plural(found.active, 'run')} in progress or waiting {'is' if found.active == 1 else 'are'}"
            " kept." if found.active else ""), type="info")
        return
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[40rem]").props(
            'role="alertdialog" aria-label="Clear all"'):
        text("h2", "Clear all runs and generated files?", "any-title")
        ui.label("This permanently deletes the following from disk. It cannot be undone.") \
            .classes("any-muted text-sm")
        with ui.element("ul").classes("list-disc pl-6 flex flex-col gap-1 text-sm").props(
                'data-testid="clear-all-list"'):
            if found.finished or found.orphans:
                n = found.finished + found.orphans
                text("li", f"{n} {plural(n, 'run')}: console logs, results, reports, screenshots, traces and saved "
                           f"states ({size_text(found.run_bytes)}), and their entries in History, Control and the "
                           "Generator")
            if not found.shared_skipped:
                for what, where, count, size in _grouped(found.shared):
                    text("li", f"{what}: {count} {plural(count, 'item')} in {where} ({size_text(size)})")
        if found.active:
            ui.label(f"Kept: {found.active} {plural(found.active, 'run')} in progress or waiting"
                     + (". The shared log, video and download folders are left alone until they finish."
                        if found.shared else ".")).classes("any-muted text-sm")
        ui.label("Kept: " + KEPT).classes("any-muted text-sm")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38") \
                .props('data-testid="clear-all-cancel"')
            ui.button(f"Delete {size_text(found.total_bytes)}", icon=icon("trash-2"), color=None,
                      on_click=lambda: dialog.submit(True)) \
                .props('data-testid="clear-all-confirm"').classes("any-danger")
    dialog.open()
    confirmed = await dialog
    dialog.delete()
    if not confirmed:
        return
    # People may have started a run while the dialog was open: clear_all re-checks and keeps it
    with ui.dialog().props("persistent") as busy, ui.card().classes("any-app any-dialog"):
        with ui.row().classes("items-center gap-3"):
            ui.spinner(size="md")
            ui.label("Deleting...")
    busy.open()
    try:
        report = await nrun.io_bound(cleanup.clear_all, svc.runs, svc.settings.repo_root, svc.settings.workspace)
    finally:
        busy.close()
        busy.delete()
    audit("run.clear_all", f"{report.runs} runs", orphans=report.orphans, shared=report.shared,
          bytes=report.bytes_freed, failed=len(report.failures), kept_active=report.kept_active)
    if on_done:
        on_done()
    await _report(report)


async def _report(report: cleanup.Report) -> None:
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[40rem]").props(
            'role="alertdialog" aria-label="Clear all finished"'):
        text("h2", "Cleared with problems" if report.failures else "Cleared", "any-title") \
            .props('data-testid="clear-all-result"')
        parts = [f"{report.runs} {plural(report.runs, 'run')}"]
        if report.orphans:
            parts.append(f"{report.orphans} leftover run {plural(report.orphans, 'folder')}")
        if report.shared:
            parts.append(f"{report.shared} shared {plural(report.shared, 'location')}")
        ui.label(f"Deleted {', '.join(parts)}; {size_text(report.bytes_freed)} freed.").classes("text-sm")
        if report.kept_active:
            ui.label(f"{report.kept_active} {plural(report.kept_active, 'run')} in progress or waiting "
                     f"{'was' if report.kept_active == 1 else 'were'} kept"
                     + ("; the shared log, video and download folders were left alone." if report.shared_skipped
                        else ".")).classes("any-muted text-sm")
        if report.failures:
            ui.label(f"{len(report.failures)} {plural(len(report.failures), 'file')} could not be deleted (in use, "
                     "or not allowed). Close whatever uses them and run Clear all again:").classes("any-warn-line")
            with ui.element("ul").classes("any-warn-list").props('data-testid="clear-all-failures"'):
                for path, why in report.failures[:50]:
                    text("li", f"{path}: {why}")
                if len(report.failures) > 50:
                    text("li", f"... and {len(report.failures) - 50} more")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Close", color=None, on_click=lambda: dialog.submit(True)).classes("any-primary") \
                .props('data-testid="clear-all-close"')
    dialog.open()
    await dialog
    dialog.delete()
