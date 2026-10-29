"""Activity: the audit trail (who did what, when), for admins."""
from __future__ import annotations

from nicegui import ui

from webapp.state import services
from webapp.ui.components import icon_button, styled_table, text


def open_activity() -> None:
    svc = services()
    entries = svc.audit.recent(500)
    rows = [{"key": i, "at": e.get("at", "").replace("T", " "), "user": e.get("user", ""),
             "action": e.get("action", ""), "target": e.get("target", ""),
             "details": ", ".join(f"{k}={v}" for k, v in e.items() if k not in ("at", "user", "action", "target"))}
            for i, e in enumerate(entries)]
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Activity"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "Audit trail", "any-eyebrow")
                text("h2", "Activity", "any-title")
            icon_button("x", "Close", dialog.close)
        ui.label(f"Newest first, from {svc.audit.path}. Sign-ins, refused actions and every change: runs, test "
                 "cases, pages, schedules and settings.").classes("any-muted text-sm")
        search = ui.input(placeholder="Filter by user, action or target").props('clearable aria-label="Filter"') \
            .classes("w-80")
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="key", pagination=20, columns=[
                {"name": "at", "label": "When", "field": "at", "align": "left", "sortable": True},
                {"name": "user", "label": "User", "field": "user", "align": "left", "sortable": True},
                {"name": "action", "label": "Action", "field": "action", "align": "left", "sortable": True},
                {"name": "target", "label": "Target", "field": "target", "align": "left"},
                {"name": "details", "label": "Details", "field": "details", "align": "left"},
            ]).classes("w-full is-compact").props('data-testid="activity-table"')
            styled_table(table, len(rows), wide=True)
            table.add_slot("body-cell-at", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.add_slot("no-data", '<div class="any-muted text-sm">Nothing recorded yet.</div>')
            search.bind_value(table, "filter")
    dialog.open()
