"""History tab: all runs with filters, details, re-run and delete."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from nicegui import ui

from webapp.services.run_manager import Run
from webapp.auth import audit, guard, ui_user
from webapp.state import services
from webapp.services.run_insights import flaky_tests
from webapp.services.schedules import TRIGGER
from webapp.ui.timeline_dialog import open_timeline
from webapp.ui.live import live
from webapp.ui.components import (
    RESULT_COLUMNS, _describe, field, icon_button, placeholder, result_slots, split_test_id,
    status_pill_template, styled_table, text,
)
from webapp.ui.clear_runs import clear_runs
from webapp.ui.console_view import ConsoleView
from webapp.ui.ai_panels import open_run_summary
from webapp.ui.quarantine_dialog import toggle_quarantine
from webapp.ui.trends_dialog import open_trends
from webapp.ui.run_compare import open_compare
from webapp.ui.test_details import open_test_details, result_rows
from webapp.ui.theme import icon

STATUSES = ["passed", "failed", "error", "cancelled", "running", "queued"]
RUN_TILES = {"passed": ("tone-pass", "circle-check"), "failed": ("tone-fail", "circle-x"),
             "error": ("tone-fail", "circle-x"), "running": ("tone-run", "loader-circle"),
             "queued": ("tone-run", "loader-circle"), "cancelled": ("tone-idle", "square-solid")}


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

        def select(options, label: str, for_id: str, key: str) -> ui.select:
            with field(label, for_id):
                return placeholder(ui.select(options, clearable=True, on_change=lambda e: self._filter(key, e.value))
                                   .props(f'for="{for_id}"').classes("w-44"), "All")

        with ui.element("div").classes("any-page-inner is-wide"):
            self.heals_view()
            self.flaky_view()
            with ui.element("section").classes("any-card").props('aria-label="Runs"'):
                with ui.element("div").classes("any-card-head"):
                    with ui.element("div").classes("any-head-left"):
                        text("h2", "Runs", "any-title")
                        self.count_pill = text("span", "", "any-count-pill")
                    with ui.element("div").classes("any-head-right"):
                        ui.button("Trends", icon=icon("chart-column"), color=None,
                                  on_click=lambda: open_trends(self.system)) \
                            .props('flat unelevated no-caps data-testid="open-trends"').classes("any-btn is-38")
                        icon_button("rotate-cw", "Reload runs", self.refresh)
                with ui.element("div").classes("any-filters"):
                    select(sorted({r.config.system for r in self.svc.runs.list()}), "System", "hist-system",
                           "system")
                    select(STATUSES, "Status", "hist-status", "status")
                    with field("Search", "hist-search"):
                        self.search = ui.input(placeholder="Search id, tags, test IDs") \
                            .props('clearable for="hist-search"').classes("w-72 max-w-full")
                self.table_view()
            self.details()
        live(self.svc.bus, ["run"], self._auto_refresh, when=lambda e: e.get("change") != "log")

    def _filter(self, key: str, value) -> None:
        setattr(self, key, value)
        self.flaky_view.refresh()
        self.table_view.refresh()

    @ui.refreshable_method
    def heals_view(self) -> None:
        """Locators the runner healed: accept (the page is updated) or reject (never used again)."""
        items = self.svc.heals.pending(self.system or None)
        if not items:
            return
        with ui.element("section").classes("any-card").props('aria-label="Healed locators" data-testid="heals"'):
            with ui.element("div").classes("any-card-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h2", "Healed locators", "any-title")
                    text("span", str(len(items)), "any-count-pill")
                ui.label("These locators found nothing in a run, and the element that best matched them was used "
                         "instead. Accept to put the new locator in the page, or reject so it is never used "
                         "again.").classes("any-muted text-sm")
            with ui.element("div").classes("any-table-wrap"):
                with ui.element("table").classes("any-table any-jr-table w-full"):
                    with ui.element("thead"):
                        with ui.element("tr"):
                            for head in ("Field", "Was", "Healed to", "Score", "Seen", ""):
                                text("th", head)
                    with ui.element("tbody"):
                        for h in items:
                            with ui.element("tr").props(f'data-testid="heal-{h.screen}-{h.field}"'):
                                with ui.element("td"):
                                    text("div", f"{h.screen}.{h.field}", "any-tc-name mono")
                                    text("div", h.system, "any-tc-code")
                                text("td", _describe(h.old), "mono any-small")
                                with ui.element("td"):
                                    text("div", _describe(h.new), "mono any-small")
                                    if h.label:
                                        text("div", f"\"{h.label}\"", "any-muted text-xs")
                                with ui.element("td"):
                                    text("div", f"{h.score}%", "any-num")
                                    text("div", h.reason, "any-muted text-xs")
                                text("td", f"{h.count}x in {len(h.runs)} run{'s' if len(h.runs) != 1 else ''}"
                                           f" ({', '.join(h.tests[:3])})", "any-small")
                                with ui.element("td"):
                                    ui.button("Accept", color=None, on_click=lambda h=h: self._heal(h, True)) \
                                        .props(f'flat dense no-caps data-testid="heal-accept-{h.field}"') \
                                        .classes("any-btn")
                                    ui.button("Reject", color=None, on_click=lambda h=h: self._heal(h, False)) \
                                        .props(f'flat dense no-caps data-testid="heal-reject-{h.field}"') \
                                        .classes("any-btn")

    def _heal(self, item, accept: bool) -> None:
        if not guard("edit", "review a healed locator", f"{item.screen}.{item.field}", system=item.system):
            return
        user = ui_user()
        try:
            if accept:
                self.svc.heals.accept(item.key, user.username if user else "anonymous")
            else:
                self.svc.heals.reject(item.key, user.username if user else "anonymous")
        except (ValueError, OSError) as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("heal.accept" if accept else "heal.reject", f"{item.system}/{item.screen}.{item.field}",
              locator=_describe(item.new))
        ui.notify(f"{item.screen}.{item.field} now uses {_describe(item.new)}" if accept else
                  f"{_describe(item.new)} will not be used for {item.field} again", type="positive")
        self.heals_view.refresh()

    @ui.refreshable_method
    def flaky_view(self) -> None:
        """Tests whose result flipped between pass and fail in their recent runs (per system/env)."""
        runs = self.svc.runs.list()
        found = []
        for system, env in sorted({(r.config.system, r.config.env) for r in runs}):
            if self.system and system != self.system:
                continue
            found += [(system, env, t) for t in flaky_tests(runs, system, env)]
        if not found:
            return
        rows = []
        for system, env, t in found:
            name, code = split_test_id(t.test_id)
            rows.append({"key": f"{system}/{env}/{t.test_id}/{t.browser}", "test_id": t.test_id, "name": name,
                         "code": code, "target": f"{system}/{env}", "browser": t.browser, "recent": t.outcomes,
                         "flips": t.flips, "failures": f"{t.failures} of {len(t.outcomes)}", "flaky": False,
                         "quarantined": self.svc.quarantine.is_quarantined(t.test_id)})
        with ui.element("section").classes("any-card").props('aria-label="Flaky tests"'):
            with ui.element("div").classes("any-card-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h2", "Flaky tests", "any-title")
                    text("span", str(len(rows)), "any-count-pill")
                ui.label("Passed and failed in their last runs, changing result at least twice. "
                         "Fix or stabilise these before trusting a red run.").classes("any-muted text-sm")
            with ui.element("div").classes("any-table-wrap"):
                table = ui.table(rows=rows, row_key="key", pagination=5, columns=[
                    {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
                    {"name": "target", "label": "System/env", "field": "target", "align": "left", "sortable": True},
                    {"name": "browser", "label": "Browser", "field": "browser", "align": "left"},
                    {"name": "recent", "label": "Recent", "field": "recent", "align": "left"},
                    {"name": "flips", "label": "Changes", "field": "flips", "align": "right", "sortable": True},
                    {"name": "failures", "label": "Failed", "field": "failures", "align": "right"},
                    {"name": "actions", "label": "", "field": "actions", "align": "right"},
                ]).classes("w-full")
                styled_table(table, len(rows), wide=True)
                result_slots(table)
                table.classes(remove="is-clickable")
                table.add_slot("body-cell-actions", '''<q-td :props="props" class="text-right">
                    <q-btn flat dense no-caps label="Timeline" :data-testid="'timeline-' + props.row.test_id"
                           @click="() => $parent.$emit('timeline', props.row)" />
                    <q-btn flat dense no-caps :label="props.row.quarantined ? 'Release' : 'Quarantine'"
                           :data-testid="'quarantine-' + props.row.test_id"
                           @click="() => $parent.$emit('quarantine', props.row)" /></q-td>''')
                table.on("quarantine", lambda e: toggle_quarantine(e.args["test_id"], self.refresh))
                table.on("timeline", lambda e: open_timeline(
                    e.args["test_id"], *str(e.args["target"]).split("/", 1), on_change=self.refresh))
                for col in ("flips", "failures"):
                    table.add_slot(f"body-cell-{col}", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')

    def refresh(self) -> None:
        self.signature = self._signature()  # a refresh made here must not be repeated by the next timer tick
        self.heals_view.refresh()
        self.flaky_view.refresh()
        self.table_view.refresh()
        self.details.refresh()

    def _auto_refresh(self) -> None:
        if self._signature() != self.signature:
            self.refresh()

    def _signature(self) -> tuple:
        return tuple((r.id, r.status, r.report_status, len(r.results)) for r in self.svc.runs.list())

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
            "scheduled": r.trigger.startswith(TRIGGER),
        } for r in self._runs()]
        self.count_pill.text = f"{len(rows)} {'run' if len(rows) == 1 else 'runs'}"
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="id", pagination=10, selection="single", columns=[
                {"name": "started", "label": "Started", "field": "started", "align": "left", "sortable": True},
                {"name": "duration", "label": "Duration", "field": "duration", "align": "left"},
                {"name": "status", "label": "Status", "field": "status", "align": "left", "sortable": True},
                {"name": "target", "label": "System/env", "field": "target", "align": "left", "sortable": True},
                {"name": "tags", "label": "Tags", "field": "tags", "align": "left"},
                {"name": "tests", "label": "Only tests", "field": "tests", "align": "left"},
                {"name": "browsers", "label": "Browsers", "field": "browsers", "align": "left"},
                {"name": "result", "label": "Result", "field": "result", "align": "left"},
            ], on_select=lambda e: self._select(e.selection)).classes("w-full is-clickable")
            styled_table(table, len(rows), wide=True)
            table.add_slot("body-cell-status", f'''<q-td :props="props">{status_pill_template()}
                <span v-if="props.row.scheduled" class="any-tag" title="Started by a schedule">scheduled</span>
                </q-td>''')
            for col in ("started", "duration", "result"):
                table.add_slot(f"body-cell-{col}", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.add_slot("no-data", '<div class="any-muted text-sm">No runs match.</div>')
            table.selected = [row for row in rows if row["id"] == self.selected]
            table.on("rowClick", lambda e: self._click(table, e.args[1]))
            self.search.bind_value(table, "filter")

    def _click(self, table, row: dict) -> None:
        """Clicking anywhere on a row selects it (not just the checkbox)."""
        table.selected = [row]
        table.update()
        self._select([row])

    def show_run(self, run_id: str) -> None:
        """Select a run and show its details (the command palette, links)."""
        self.selected = run_id if self.svc.runs.get(run_id) else None
        self.table_view.refresh()
        self.details.refresh()

    def _select(self, selection: list[dict]) -> None:
        self.selected = selection[0]["id"] if selection else None
        self.details.refresh()

    @ui.refreshable_method
    def details(self) -> None:
        run = self.svc.runs.get(self.selected) if self.selected else None
        if not run:
            with ui.element("div").classes("any-card any-empty"):
                ui.label("Select a run to see its details.").classes("any-muted")
            return
        cfg = run.config
        with ui.element("section").classes("any-card").props('aria-label="Run details"'):
            with ui.element("div").classes("any-run-head"):
                with ui.element("div").classes("any-run-id"):
                    tile_tone, tile_icon = RUN_TILES.get(run.status, ("tone-idle", "circle-play"))
                    with ui.element("div").classes(f"any-tile {tile_tone}"):
                        ui.icon(icon(tile_icon)).props('aria-hidden="true"')
                    with ui.element("div").classes("min-w-0"):
                        text("h2", "Run", "any-eyebrow")
                        with ui.element("div").classes("any-run-title"):
                            text("span", run.id, "any-headline mono is-id")
                            self._pill(run.status)
                        with ui.element("div").classes("any-meta"):
                            with ui.element("span"):
                                text("span", "Duration")
                                text("span", duration(run) or "—", "v")
                with ui.element("div").classes("any-run-actions"):
                    if run.active:
                        ui.button("Watch live", icon=icon("circle-play"), color=None,
                                  on_click=lambda: self.on_watch(run.id)) \
                            .props("flat unelevated no-caps").classes("any-btn is-38")
                    ui.button("Re-run", icon=icon("rotate-cw"), color=None, on_click=lambda: self.rerun(run.id)) \
                        .props("unelevated no-caps").classes("any-primary")
                    ui.button("Clear", icon=icon("trash-2"), color=None, on_click=lambda: self.clear(run.id)) \
                        .props('flat unelevated no-caps data-testid="clear-runs"').classes("any-btn is-38 is-danger") \
                        .tooltip("Delete the logs and reports of this run, or of all finished runs") \
                        .set_enabled(not run.active)
                    ui.button("Report", icon=icon("chart-column"), color=None,
                              on_click=lambda: self.open_summary(run.id)) \
                        .props('flat unelevated no-caps data-testid="run-report"').classes("any-btn is-38") \
                        .set_enabled(not run.active)
                    if self.svc.ai.enabled():
                        ui.button("AI summary", icon=icon("sparkles"), color=None,
                                  on_click=lambda: guard("ai.read", "summarize") and open_run_summary(run)) \
                            .props('flat unelevated no-caps data-testid="ai-summary"').classes("any-btn is-38") \
                            .set_enabled(not run.active)
                    ui.button("Compare", icon=icon("list-checks"), color=None,
                              on_click=lambda: open_compare(run.id)) \
                        .props("flat unelevated no-caps").classes("any-btn is-38").set_enabled(not run.active)
                    if run.report_status == "ready" or self.svc.runs.allure_available():
                        report = ui.button("Allure", icon=icon("external-link"), color=None,
                                           on_click=lambda: ui.navigate.to(
                                               f"/runs/{run.id}/allure-report/index.html", new_tab=True)) \
                            .props("flat unelevated no-caps").classes("any-btn is-38")
                        report.set_enabled(run.report_status == "ready")
                        if run.report_status != "ready" and run.report_message:
                            report.tooltip(run.report_message)
                    ui.button("Console log", icon=icon("file-text"), color=None,
                              on_click=lambda: ui.navigate.to(f"/runs/{run.id}/console.log", new_tab=True)) \
                        .props("flat unelevated no-caps").classes("any-btn is-38")
            if run.error:
                with ui.element("div").classes("any-callout").props('role="status"'):
                    ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
                    with ui.element("div").classes("any-callout-body"):
                        ui.label(run.error).classes("any-callout-title")
            with ui.element("dl").classes("any-facts"):
                options = ", ".join(f"{k}={v}" for k, v in cfg.options.items()) or "-"
                for label, value in (("System", cfg.system), ("Environment", cfg.env or "-"),
                                     ("Tags", ", ".join(cfg.tags)), ("Browsers", ", ".join(cfg.browsers)),
                                     ("Workers", str(cfg.workers)), ("Only tests", ", ".join(cfg.test_ids) or "-"),
                                     ("Exit code", str(run.exit_code)), ("Started by", self._started_by(run)),
                                     ("Options", options)):
                    with ui.element("div").classes("any-fact" + (" is-wide" if label == "Options" else "")):
                        text("dt", label, "any-eyebrow")
                        text("dd", value)
            if not run.active and any(r.outcome in ("failed", "error") for r in run.results.values()):
                self.causes(run)
            rows = result_rows(run)
            with ui.element("div").classes("any-table-wrap"):
                table = ui.table(rows=rows, row_key="nodeid", pagination=10, columns=RESULT_COLUMNS).classes("w-full")
                styled_table(table, len(rows), wide=True)
                result_slots(table)
                table.on("rowClick", lambda e, run=run: open_test_details(run, e.args[1]))
            with ui.expansion("Console output (last 300 lines)", icon=icon("terminal")) \
                    .props(f'expand-icon="{icon("chevron-down")}"').classes("any-disclosure is-console"):
                with ui.element("div").classes("any-console is-inline"):
                    ConsoleView(max_lines=300, lines=run.log[-300:] or ["(empty)"])

    def causes(self, run: Run) -> None:
        """The run's failures grouped by cause, each with what the evidence points to (failure_intel)."""
        from webapp.services.failure_intel import for_services
        try:
            groups = for_services(self.svc).groups(run.config.system, run.config.env, run_id=run.id)
        except Exception:  # noqa: BLE001 - the groups are a help; the results table below still shows everything
            return
        if not groups:
            return
        n = sum(len(g.failures) for g in groups)
        with ui.element("div").classes("any-causes").props('aria-label="Why it failed" data-testid="causes"'):
            with ui.element("div").classes("any-head-left"):
                text("h3", "Why it failed", "any-title is-sm")
                causes = f"{len(groups)} cause{'' if len(groups) == 1 else 's'}"
                text("span", f"{n} failure{'' if n == 1 else 's'}, {causes}", "any-muted text-sm")
            for g in groups[:8]:
                with ui.element("div").classes("any-cause").props(f'data-kind="{g.kind}"'):
                    text("div", g.title, "any-cause-title")
                    if g.explanation:
                        with ui.element("ul").classes("any-cause-why"):
                            for line in g.explanation:
                                text("li", line)
                    if g.suggestion:
                        text("div", g.suggestion, "any-cause-fix")
                    with ui.element("div").classes("any-comp-fields"):
                        for t in g.tests[:12]:
                            text("span", t, "any-tag mono")
                        if len(g.tests) > 12:
                            text("span", f"+{len(g.tests) - 12} more", "any-muted text-xs")
                    if g.examples_message:
                        with ui.expansion("Error", icon=icon("terminal")).classes("any-disclosure"):
                            text("pre", g.examples_message, "any-code")
            if len(groups) > 8:
                text("div", f"{len(groups) - 8} more causes: see the results below.", "any-muted text-xs")

    def _started_by(self, run: Run) -> str:
        if not run.trigger.startswith(TRIGGER):
            return "A person"
        schedule = self.svc.schedules.get(run.trigger[len(TRIGGER):])
        return f"Schedule: {schedule.name}" if schedule else "A schedule (since deleted)"

    @staticmethod
    def _pill(status: str) -> None:
        tone = {"passed": "passed", "failed": "failed", "error": "failed", "running": "running"}.get(status, "idle")
        with ui.element("span").classes(f"any-pill pill-{tone}"):
            ui.element("span").classes("dot").props('aria-hidden="true"')
            text("span", status)

    def open_summary(self, run_id: str) -> None:
        run = self.svc.runs.get(run_id)
        if run and self.svc.runs.write_summary(run):
            ui.navigate.to(f"/runs/{run.id}/report.html", new_tab=True)
        else:
            ui.notify("The report could not be written (see the server log).", type="negative")

    def rerun(self, run_id: str) -> None:
        run = self.svc.runs.get(run_id)
        if not guard("run", "re-run", run_id, system=run.config.system if run else None,
                     env=run.config.env if run else None):
            return
        try:
            run = self.svc.runs.rerun(run_id)
        except (ValueError, RuntimeError) as e:
            ui.notify(str(e), type="negative")
            return
        audit("run.rerun", run.id, of=run_id)
        position = self.svc.runs.queue_position(run.id)
        ui.notify(f"Run {run.id} queued (#{position})" if position else f"Run {run.id} started",
                  type="info" if position else "positive")
        self.on_watch(run.id)

    def runs_cleared(self) -> None:
        """After Clear all: forget a selection that is gone and redraw the runs."""
        if self.selected and not self.svc.runs.get(self.selected):
            self.selected = None
        self.refresh()

    async def clear(self, run_id: str) -> None:
        deleted = await clear_runs(self.svc.runs.get(run_id))
        if deleted:
            self.selected = None
            self.refresh()
