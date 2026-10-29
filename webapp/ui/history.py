"""History tab: all runs with filters, details, re-run and delete."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from nicegui import ui

from webapp.services.run_manager import Run
from webapp.state import services
from webapp.ui.generator import confirm

STATUSES = ["passed", "failed", "error", "cancelled", "running", "queued"]
STATUS_COLORS = {"queued": "grey", "running": "blue", "passed": "positive", "failed": "negative",
                 "error": "orange", "cancelled": "grey"}


def duration(run: Run) -> str:
    try:
        start = datetime.fromisoformat(run.started_at)
        end = datetime.fromisoformat(run.finished_at) if run.finished_at else datetime.now()
    except ValueError:
        return ""
    secs = int((end - start).total_seconds())
    return f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"


class HistoryPanel:
    def __init__(self, on_watch: Callable[[str], None]):
        self.svc = services()
        self.on_watch = on_watch  # switch to the Control tab and follow a run
        self.selected: str | None = None
        self.system: str | None = None
        self.status: str | None = None
        self.signature: tuple = ()

        with ui.row().classes("w-full items-center gap-3"):
            ui.select(sorted({r.config.system for r in self.svc.runs.list()}), label="System", clearable=True,
                      on_change=lambda e: self._filter("system", e.value)).props("dense").classes("w-40")
            ui.select(STATUSES, label="Status", clearable=True,
                      on_change=lambda e: self._filter("status", e.value)).props("dense").classes("w-40")
            self.search = ui.input(placeholder="Search id, tags, test IDs").props("dense clearable").classes("w-64")
            ui.space()
            ui.button(icon="refresh", on_click=self.refresh).props("flat round").tooltip("Reload")
        self.table_view()
        self.details()
        ui.timer(2.0, self._auto_refresh)

    def _filter(self, key: str, value) -> None:
        setattr(self, key, value)
        self.table_view.refresh()

    def refresh(self) -> None:
        self.table_view.refresh()
        self.details.refresh()

    def _auto_refresh(self) -> None:
        sig = tuple((r.id, r.status, r.report_status, len(r.results)) for r in self.svc.runs.list())
        if sig != self.signature:
            self.signature = sig
            self.refresh()

    def _runs(self) -> list[Run]:
        return [r for r in self.svc.runs.list()
                if (not self.system or r.config.system == self.system) and (not self.status or r.status == self.status)]

    @ui.refreshable_method
    def table_view(self) -> None:
        rows = [{
            "id": r.id, "started": (r.started_at or r.created_at).replace("T", " "), "duration": duration(r),
            "status": r.status, "target": f"{r.config.system}/{r.config.env}", "tags": ", ".join(r.config.tags),
            "tests": ", ".join(r.config.test_ids), "browsers": ", ".join(r.config.browsers),
            "result": " ".join(f"{k}:{v}" for k, v in sorted(r.counts().items())),
        } for r in self._runs()]
        table = ui.table(rows=rows, row_key="id", pagination=10, selection="single", columns=[
            {"name": "started", "label": "Started", "field": "started", "align": "left", "sortable": True},
            {"name": "duration", "label": "Duration", "field": "duration", "align": "left"},
            {"name": "status", "label": "Status", "field": "status", "align": "left", "sortable": True},
            {"name": "target", "label": "System/env", "field": "target", "align": "left", "sortable": True},
            {"name": "tags", "label": "Tags", "field": "tags", "align": "left"},
            {"name": "tests", "label": "Only tests", "field": "tests", "align": "left"},
            {"name": "browsers", "label": "Browsers", "field": "browsers", "align": "left"},
            {"name": "result", "label": "Result", "field": "result", "align": "left"},
        ], on_select=lambda e: self._select(e.selection)).classes("w-full").props("dense flat")
        table.add_slot("body-cell-status", r'''
            <q-td :props="props">
              <q-badge :color="{passed:'positive',failed:'negative',error:'orange',running:'blue'}[props.value] || 'grey'"
                       :label="props.value" />
            </q-td>''')
        table.selected = [row for row in rows if row["id"] == self.selected]
        table.on("rowClick", lambda e: self._click(table, e.args[1]))
        self.search.bind_value(table, "filter")
        if not rows:
            ui.label("No runs match.").classes("text-sm text-grey-7")

    def _click(self, table, row: dict) -> None:
        """Clicking anywhere on a row selects it (not just the checkbox)."""
        table.selected = [row]
        table.update()
        self._select([row])

    def _select(self, selection: list[dict]) -> None:
        self.selected = selection[0]["id"] if selection else None
        self.details.refresh()

    @ui.refreshable_method
    def details(self) -> None:
        run = self.svc.runs.get(self.selected) if self.selected else None
        if not run:
            ui.label("Select a run to see its details.").classes("text-sm text-grey-7 mt-2")
            return
        cfg = run.config
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label(run.id).classes("text-lg font-mono")
                ui.badge(run.status, color=STATUS_COLORS.get(run.status, "grey"))
                ui.label(duration(run)).classes("text-sm text-grey-7")
                ui.space()
                if run.active:
                    ui.button("Watch live", icon="visibility", on_click=lambda: self.on_watch(run.id)) \
                        .props("flat no-caps")
                ui.button("Re-run", icon="replay", on_click=lambda: self.rerun(run.id)).props("no-caps") \
                    .set_enabled(not any(r.active for r in self.svc.runs.list()))
                report = ui.button("Allure report", icon="assessment",
                                   on_click=lambda: ui.navigate.to(f"/runs/{run.id}/allure-report/index.html",
                                                                   new_tab=True)).props("outline no-caps")
                report.set_enabled(run.report_status == "ready")
                if run.report_status != "ready" and run.report_message:
                    report.tooltip(run.report_message)
                ui.button("Console log", icon="description",
                          on_click=lambda: ui.navigate.to(f"/runs/{run.id}/console.log", new_tab=True)) \
                    .props("flat no-caps")
                ui.button(icon="delete", color="negative", on_click=lambda: self.delete(run.id)) \
                    .props("flat round").tooltip("Delete run").set_enabled(not run.active)
            if run.error:
                ui.label(run.error).classes("text-sm text-orange-8")
            with ui.grid(columns=4).classes("w-full gap-x-6 gap-y-1 text-sm"):
                options = ", ".join(f"{k}={v}" for k, v in cfg.options.items()) or "-"
                for label, value in (("System", cfg.system), ("Environment", cfg.env or "-"),
                                     ("Tags", ", ".join(cfg.tags)), ("Browsers", ", ".join(cfg.browsers)),
                                     ("Workers", str(cfg.workers)), ("Only tests", ", ".join(cfg.test_ids) or "-"),
                                     ("Exit code", str(run.exit_code)), ("Options", options)):
                    with ui.column().classes("gap-0"):
                        ui.label(label).classes("text-xs text-grey-7")
                        ui.label(value).classes("break-all")
            rows = [{"nodeid": r.nodeid, "outcome": r.outcome, "test_id": r.test_id, "browser": r.browser,
                     "duration": r.duration, "message": r.message} for r in list(run.results.values())]
            table = ui.table(rows=rows, row_key="nodeid", pagination=10, columns=[
                {"name": "outcome", "label": "Status", "field": "outcome", "align": "left", "sortable": True},
                {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
                {"name": "browser", "label": "Browser", "field": "browser", "align": "left"},
                {"name": "duration", "label": "Seconds", "field": "duration", "sortable": True},
                {"name": "message", "label": "Message", "field": "message", "align": "left"},
            ]).classes("w-full").props("dense flat")
            table.add_slot("body-cell-outcome", r'''
                <q-td :props="props">
                  <q-badge :color="{passed:'positive',failed:'negative',error:'orange',running:'blue'}[props.value] || 'grey'"
                           :label="props.value" />
                </q-td>''')
            with ui.expansion("Console output (last 300 lines)", icon="terminal").classes("w-full"):
                log = ui.log(max_lines=300).classes("w-full h-80 text-xs")
                log.push("\n".join(run.log[-300:]) or "(empty)")

    def rerun(self, run_id: str) -> None:
        try:
            run = self.svc.runs.rerun(run_id)
        except (ValueError, RuntimeError) as e:
            ui.notify(str(e), type="negative")
            return
        ui.notify(f"Run {run.id} started", type="positive")
        self.on_watch(run.id)

    async def delete(self, run_id: str) -> None:
        if not await confirm(f"Delete run {run_id} and its files?", "Delete", "negative"):
            return
        if self.svc.runs.delete(run_id):
            ui.notify("Run deleted", type="positive")
            self.selected = None
            self.refresh()
        else:
            ui.notify("Run could not be deleted (still running?)", type="negative")
