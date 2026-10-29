"""Control tab: pick system/env/tags/browsers, preview tests, run, watch live, open Allure."""
from __future__ import annotations

from nicegui import ui

from webapp.services.run_manager import Run, RunConfig
from webapp.state import services

BROWSERS = ["chrome", "msedge", "chromium", "firefox", "webkit"]
LOG_LINES_SHOWN = 1500
LOG_LINES_PER_TICK = 400
ACTIVE = ("queued", "running")

STATUS_COLORS = {
    "queued": "grey", "running": "blue", "passed": "positive", "failed": "negative",
    "error": "orange", "cancelled": "grey",
}


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes", "y")


def _int(value: str | None, default: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


class ControlPanel:
    def __init__(self):
        self.svc = services()
        cfg = self.svc.env.load()
        systems = sorted(set(self.svc.systems.systems()) | set(self.svc.catalog.systems_with_tests()))
        system = cfg["systemConfig"] if cfg["systemConfig"] in systems else (systems[0] if systems else None)

        self.systems = systems
        self.catalog = self.svc.catalog.load(system)
        self.run_id: str | None = None
        self.log_pos = 0
        self.last_signature: tuple = ()
        self.show_selected_only = True

        self._build(cfg, system)
        latest = next(iter(self.svc.runs.list()), None)
        if latest:
            self.view_run(latest.id)
        ui.timer(0.5, self.tick)

    # ───────────── layout ─────────────

    def _build(self, cfg: dict[str, str], system: str | None) -> None:
        with ui.row().classes("w-full items-start gap-4 no-wrap max-lg:flex-wrap"):
            with ui.column().classes("w-[26rem] max-lg:w-full gap-4 shrink-0"):
                self._build_setup(cfg, system)
            with ui.column().classes("flex-1 min-w-0 gap-4 max-lg:w-full"):
                self._build_tests()
                self._build_run()
                self._build_history()

    def _build_setup(self, cfg: dict[str, str], system: str | None) -> None:
        with ui.card().classes("w-full"):
            ui.label("Run setup").classes("text-lg font-medium")
            self.system_select = ui.select(self.systems, label="System", value=system,
                                           on_change=self.on_system_change).classes("w-full")
            envs = self.svc.systems.environments(system)
            env = cfg["envConfig"] if cfg["envConfig"] in envs else (envs[0] if envs else None)
            self.env_select = ui.select(envs, label="Environment", value=env,
                                        on_change=lambda: self.credentials.refresh()).classes("w-full")

            tags = _split(cfg["runTags"])
            self.tags_select = ui.select(
                sorted(set(self.catalog.all_tags()) | set(tags), key=str.casefold), label="Tags",
                value=tags, multiple=True, with_input=True, new_value_mode="add-unique", clearable=True,
                on_change=lambda: self.tests_view.refresh(),
            ).props("use-chips").classes("w-full")

            browsers = [b.lower() for b in _split(cfg["browsers"])] or ["chrome"]
            self.browsers_select = ui.select(
                BROWSERS + [b for b in browsers if b not in BROWSERS], label="Browsers",
                value=browsers, multiple=True, on_change=lambda: self.tests_view.refresh(),
            ).props("use-chips").classes("w-full")
            self.workers = ui.number("Parallel workers (pytest-xdist)", value=1, min=1, max=8,
                                     precision=0).classes("w-full")

            with ui.expansion("Browser & artifacts", icon="tune").classes("w-full"):
                self.headless = ui.switch("Headless", value=_truthy(cfg["headlessMode"]))
                self.maximized = ui.switch("Maximized window", value=_truthy(cfg["browserConfigMaximized"]))
                self.slowmo = ui.number("Slow-mo (ms)", value=_int(cfg["slowmo"], 0), min=0, step=50,
                                        precision=0).classes("w-full")
                waits = ["load", "domcontentloaded", "networkidle", "commit"]
                self.wait_until = ui.select(waits, label="Page wait until",
                                            value=cfg["pageWaitUntil"] if cfg["pageWaitUntil"] in waits
                                            else "load").classes("w-full")
                self.recording = ui.switch("Record video", value=_truthy(cfg["recording"]))
                self.tracing = ui.switch("Playwright trace", value=_truthy(cfg["tracing"]))
                self.snapshots = ui.switch("Snapshots", value=_truthy(cfg["snapshots"]))

            self.credentials()

            with ui.row().classes("w-full gap-2 mt-2"):
                self.run_button = ui.button("Run", icon="play_arrow", on_click=self.start_run)
                self.cancel_button = ui.button("Cancel", icon="stop", color="negative",
                                               on_click=self.cancel_run).props("outline")
                ui.space()
                ui.button("Save as defaults", icon="save", on_click=self.save_defaults).props("flat")
            ui.label(f".env: {self.svc.env.env_path}").classes("text-xs text-grey-6 break-all")

    @ui.refreshable_method
    def credentials(self) -> None:
        status = self.svc.credential_status(self.system_select.value, self.env_select.value)
        with ui.column().classes("w-full gap-1"):
            ui.label("Login credentials").classes("text-sm font-medium mt-2")
            if status is None:
                ui.label("No login entry in systemData.json for this system/environment.") \
                    .classes("text-xs text-orange-8")
            elif not status:
                ui.label("No credentials needed.").classes("text-xs text-grey-7")
            for item in status or []:
                with ui.row().classes("items-center gap-2"):
                    ok = item["set"]
                    ui.icon("check_circle" if ok else "warning", color="positive" if ok else "orange")
                    ui.label(item["key"]).classes("font-mono text-xs")
                    ui.label("set" if ok else "missing - add it to .env").classes("text-xs text-grey-7")

    def _build_tests(self) -> None:
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center"):
                ui.label("Tests").classes("text-lg font-medium")
                ui.space()
                ui.switch("Selected only", value=True,
                          on_change=lambda e: self._set_selected_only(e.value))
                ui.button(icon="refresh", on_click=self.reload_catalog).props("flat round") \
                    .tooltip("Reload test files")
            self.tests_view()

    @ui.refreshable_method
    def tests_view(self) -> None:
        selected = {id(t) for t in self.catalog.select(self.tags_select.value or [])}
        browser_count = max(len(self.browsers_select.value or []), 1)
        ui.label(f"{len(selected)} of {len(self.catalog.tests)} test case(s) selected"
                 f" × {browser_count} browser(s) = {len(selected) * browser_count} run(s)") \
            .classes("text-sm text-grey-8")
        for err in self.catalog.errors:
            ui.label(f"Could not read {err}").classes("text-xs text-negative")
        dupes = sorted({t.test_id for t in self.catalog.tests if t.duplicate})
        if dupes:
            ui.label(f"Duplicate test IDs: {', '.join(dupes)}").classes("text-xs text-orange-8")
        rows = [
            {"key": i, "run": id(t) in selected, "test_id": t.test_id, "tags": ", ".join(t.tags),
             "file": t.file, "steps": t.steps}
            for i, t in enumerate(self.catalog.tests)
            if not self.show_selected_only or id(t) in selected
        ]
        table = ui.table(rows=rows, row_key="key", pagination=10, columns=[
            {"name": "run", "label": "", "field": "run", "align": "center"},
            {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
            {"name": "tags", "label": "Tags", "field": "tags", "align": "left"},
            {"name": "file", "label": "File", "field": "file", "align": "left", "sortable": True},
            {"name": "steps", "label": "Steps", "field": "steps", "sortable": True},
        ]).classes("w-full").props("dense flat")
        table.add_slot("body-cell-run", r'''
            <q-td :props="props">
              <q-icon :name="props.value ? 'check' : 'remove'" :color="props.value ? 'positive' : 'grey-5'" />
            </q-td>''')

    def _build_run(self) -> None:
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("Current run").classes("text-lg font-medium")
                self.run_title = ui.label("-").classes("font-mono text-sm text-grey-8")
                self.status_badge = ui.badge("idle", color="grey")
                ui.space()
                self.report_button = ui.button("Allure report", icon="assessment",
                                               on_click=self.open_report).props("outline")
                self.console_button = ui.button("Console log", icon="description",
                                                on_click=self.open_console).props("flat")
            self.progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            self.summary = ui.label("").classes("text-sm text-grey-8")
            self.message = ui.label("").classes("text-sm text-orange-8 break-all")
            self.results_table = ui.table(rows=[], row_key="nodeid", pagination=10, columns=[
                {"name": "outcome", "label": "Status", "field": "outcome", "align": "left", "sortable": True},
                {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
                {"name": "browser", "label": "Browser", "field": "browser", "align": "left"},
                {"name": "duration", "label": "Seconds", "field": "duration", "sortable": True},
                {"name": "message", "label": "Message", "field": "message", "align": "left",
                 "classes": "max-w-md truncate"},
            ]).classes("w-full").props("dense flat")
            self.results_table.add_slot("body-cell-outcome", r'''
                <q-td :props="props">
                  <q-badge :color="{passed:'positive',failed:'negative',error:'orange',running:'blue'}[props.value] || 'grey'"
                           :label="props.value" />
                </q-td>''')
            self.log_view = ui.log(max_lines=LOG_LINES_SHOWN).classes("w-full h-96 text-xs")
        self._update_buttons(None)

    @ui.refreshable_method
    def history(self) -> None:
        runs = self.svc.runs.list()[:25]
        rows = [{
            "id": r.id, "status": r.status, "system": r.config.system, "env": r.config.env,
            "tags": ", ".join(r.config.tags), "browsers": ", ".join(r.config.browsers),
            "result": " ".join(f"{k}:{v}" for k, v in sorted(r.counts().items())),
            "started": (r.started_at or r.created_at).replace("T", " "),
        } for r in runs]
        if not rows:
            ui.label("No runs yet.").classes("text-sm text-grey-7")
            return
        table = ui.table(rows=rows, row_key="id", pagination=5, columns=[
            {"name": "started", "label": "Started", "field": "started", "align": "left", "sortable": True},
            {"name": "status", "label": "Status", "field": "status", "align": "left"},
            {"name": "system", "label": "System", "field": "system", "align": "left"},
            {"name": "env", "label": "Env", "field": "env", "align": "left"},
            {"name": "tags", "label": "Tags", "field": "tags", "align": "left"},
            {"name": "browsers", "label": "Browsers", "field": "browsers", "align": "left"},
            {"name": "result", "label": "Result", "field": "result", "align": "left"},
        ]).classes("w-full cursor-pointer").props("dense flat")
        table.on("rowClick", lambda e: self.view_run(e.args[1]["id"]))

    def _build_history(self) -> None:
        with ui.card().classes("w-full"):
            ui.label("Run history").classes("text-lg font-medium")
            ui.label("Click a run to view its results and log.").classes("text-xs text-grey-7")
            self.history()

    # ───────────── actions ─────────────

    def on_system_change(self) -> None:
        envs = self.svc.systems.environments(self.system_select.value)
        self.env_select.set_options(envs, value=envs[0] if envs else None)
        self.reload_catalog()
        self.credentials.refresh()

    def reload_catalog(self) -> None:
        self.catalog = self.svc.catalog.load(self.system_select.value)
        current = self.tags_select.value or []
        self.tags_select.set_options(sorted(set(self.catalog.all_tags()) | set(current), key=str.casefold),
                                     value=current)
        self.tests_view.refresh()

    def _set_selected_only(self, value: bool) -> None:
        self.show_selected_only = value
        self.tests_view.refresh()

    def _options(self) -> dict[str, object]:
        return {
            "headlessMode": bool(self.headless.value),
            "browserConfigMaximized": bool(self.maximized.value),
            "slowmo": int(self.slowmo.value or 0),
            "pageWaitUntil": self.wait_until.value or "load",
            "recording": bool(self.recording.value),
            "tracing": bool(self.tracing.value),
            "snapshots": bool(self.snapshots.value),
        }

    def start_run(self) -> None:
        tags = self.tags_select.value or []
        browsers = self.browsers_select.value or ["chrome"]
        selected = self.catalog.select(tags)
        if not selected:
            ui.notify("No test cases match the selected tags.", type="warning")
            return
        status = self.svc.credential_status(self.system_select.value, self.env_select.value)
        if status is None:
            ui.notify("No login entry in systemData.json for this system/environment.", type="negative")
            return
        missing = [c["key"] for c in status if not c["set"]]
        if missing:
            ui.notify(f"Missing credentials in .env: {', '.join(missing)} - login steps may fail.",
                      type="warning", timeout=8000)
        config = RunConfig(system=self.system_select.value or "", env=self.env_select.value or "",
                           tags=list(tags), browsers=list(browsers),
                           workers=int(self.workers.value or 1), options=self._options())
        try:
            run = self.svc.runs.start(config, expected=len(selected) * len(browsers))
        except (ValueError, RuntimeError) as e:
            ui.notify(str(e), type="negative")
            return
        ui.notify(f"Run {run.id} started", type="positive")
        self.view_run(run.id)
        self.history.refresh()

    def cancel_run(self) -> None:
        if self.run_id and self.svc.runs.cancel(self.run_id):
            ui.notify("Cancelling run...", type="info")

    def save_defaults(self) -> None:
        values = {
            "systemConfig": self.system_select.value or "",
            "envConfig": self.env_select.value or "",
            "runTags": self.tags_select.value or [],
            "browsers": self.browsers_select.value or ["chrome"],
            **self._options(),
        }
        try:
            self.svc.env.save(values)
        except (ValueError, OSError) as e:
            ui.notify(f"Could not save .env: {e}", type="negative")
            return
        ui.notify("Saved as defaults in .env", type="positive")

    def open_report(self) -> None:
        run = self._run()
        if run and run.report_status == "ready":
            ui.navigate.to(f"/runs/{run.id}/allure-report/index.html", new_tab=True)
        elif run:
            ui.notify(run.report_message or f"Report is {run.report_status or 'not ready'}.",
                      type="warning", timeout=10000, multi_line=True)

    def open_console(self) -> None:
        if self.run_id:
            ui.navigate.to(f"/runs/{self.run_id}/console.log", new_tab=True)

    # ───────────── live view ─────────────

    def _run(self) -> Run | None:
        return self.svc.runs.get(self.run_id) if self.run_id else None

    def view_run(self, run_id: str) -> None:
        self.run_id = run_id
        self.log_pos = 0
        self.last_signature = ()
        self.log_view.clear()
        self.tick()

    def tick(self) -> None:
        run = self._run()
        self._update_buttons(run)
        if not run:
            return
        lines, self.log_pos = run.log_since(self.log_pos)
        if lines:
            self.log_view.push("\n".join(lines[-LOG_LINES_PER_TICK:]))

        counts = run.counts()
        signature = (run.status, run.report_status, len(run.results), tuple(sorted(counts.items())))
        if signature == self.last_signature:
            return
        was_active = bool(self.last_signature) and self.last_signature[0] in ACTIVE
        self.last_signature = signature

        self.run_title.text = f"{run.id} · {run.config.system}/{run.config.env} · {', '.join(run.config.tags)}"
        self.status_badge.text = run.status
        self.status_badge.props(f"color={STATUS_COLORS.get(run.status, 'grey')}")
        done = sum(v for k, v in counts.items() if k != "running")
        expected = max(run.expected, len(run.results), 1)
        self.progress.value = min(done / expected, 1.0) if run.active else 1.0
        if counts:
            breakdown = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            self.summary.text = f"{done}/{run.expected or '?'} done · {breakdown}"
        else:
            self.summary.text = "Starting..." if run.active else "No test results recorded."
        report_problem = run.report_message if run.report_status in ("unavailable", "failed") else ""
        self.message.text = run.error or report_problem
        self.results_table.rows = [
            {"nodeid": r.nodeid, "outcome": r.outcome, "test_id": r.test_id, "browser": r.browser,
             "duration": r.duration, "message": r.message}
            for r in list(run.results.values())
        ]
        self.results_table.update()
        if was_active and not run.active:
            self.history.refresh()
            ui.notify(f"Run {run.id} finished: {run.status}",
                      type="positive" if run.status == "passed" else "warning")

    def _update_buttons(self, run: Run | None) -> None:
        self.run_button.set_enabled(not any(r.active for r in self.svc.runs.list()))
        self.cancel_button.set_enabled(bool(run and run.active))
        self.report_button.set_enabled(bool(run and run.report_status == "ready"))
        self.console_button.set_enabled(bool(run))

