"""Control tab: pick system/env/tags/browsers, preview tests, run, watch live, open Allure."""
from __future__ import annotations

import re

from nicegui import ui

from webapp.services.run_manager import Run, RunConfig
from webapp.auth import audit, guard
from webapp.state import services
from webapp.ui.components import (
    RESULT_COLUMNS, copy_button, eyebrow, field, fmt_seconds, icon_button, placeholder, plural, result_slots,
    split_test_id, status_pill_template, styled_table, test_case_template, text, tone,
)
from webapp.ui.console_view import ConsoleView
from webapp.ui.test_details import open_test_details, result_rows
from webapp.ui.theme import icon, svg

BROWSERS = ["chrome", "msedge", "chromium", "firefox", "webkit"]
LOG_LINES_SHOWN = 1500
LOG_LINES_PER_TICK = 400
ACTIVE = ("queued", "running")
WORKERS_MIN, WORKERS_MAX = 1, 8
DENSE_BAR_RUNS = 12  # above this, duration-bar segments drop their labels (tooltips stay)

# Run status -> (tile tone, tile icon, headline)
RUN_LOOK = {
    "passed": ("tone-pass", "circle-check", "Passed"),
    "failed": ("tone-fail", "circle-x", "Failed"),
    "error": ("tone-fail", "circle-x", "Error"),
    "running": ("tone-run", "loader-circle", "Running"),
    "queued": ("tone-run", "loader-circle", "Queued"),
    "cancelled": ("tone-idle", "square-solid", "Cancelled"),
}
IDLE_LOOK = ("tone-idle", "circle-play", "Idle")
# Legend order; passed and failed are always shown
LEGEND = [("passed", "passed", "passed"), ("failed", "failed", "failed"), ("error", "error", "error"),
          ("running", "running", "running"), ("queued", "idle", "queued"), ("not run", "idle", "not run"),
          ("skipped", "idle", "skipped"), ("cancelled", "idle", "cancelled"), ("xfailed", "idle", "xfailed"),
          ("xpassed", "passed", "xpassed")]
ALLURE_RE = re.compile(r"^(?P<title>.+? not found)\. (?P<body>.*?)(?: Raw results: (?P<path>.+))?$")


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes", "y")


def _int(value: str | None, default: int) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _chip_slot(kind: str) -> str:
    """selected-item slot for the multi-selects: chip + remove button."""
    mono = " mono" if kind == "tag" else ""
    leading = svg("globe", "any-chip-icon") if kind == "browser" else ""
    return (f'<span class="any-chip{mono}">{leading}<span>{{{{ props.opt.label ?? props.opt }}}}</span>'
            f'<button type="button" class="any-chip-x" tabindex="0" '
            f':aria-label="\'Remove {kind} \' + (props.opt.label ?? props.opt)" '
            # QSelect opens its menu on mousedown and re-renders the chips; keep mousedown off the control
            f'@mousedown.stop.prevent @touchstart.stop @click.stop="props.removeAtIndex(props.index)" '
            f'@keydown.enter.stop.prevent="props.removeAtIndex(props.index)">'
            f'{svg("x")}</button></span>')


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
        # Test IDs unchecked in the Tests table. Only tag-matched tests can be checked: the suite filters by tags
        # first and by test ID second, so an empty set keeps the old behaviour (run everything the tags match).
        self.excluded: set[str] = set()
        self.planned_runs = 0
        self._busy: bool | None = None
        self._allure = self.svc.runs.allure_available()  # the Allure button only shows when the CLI exists

        self._build(cfg, system)
        latest = next(iter(self.svc.runs.list()), None)
        if latest:
            self.view_run(latest.id)
        else:
            self._show_idle()
        ui.timer(0.5, self.tick)

    # ───────────── layout ─────────────

    def _build(self, cfg: dict[str, str], system: str | None) -> None:
        with ui.element("div").classes("any-shell"):
            with ui.element("aside").classes("any-sidebar").props('aria-label="Run setup"'):
                self._build_setup(cfg, system)
            with ui.element("main").classes("any-main"):
                with ui.element("div").classes("any-main-inner"):
                    self._build_tests()
                    self._build_run()
                    self._build_history()

    def _select(self, options, value, for_id: str, **kwargs) -> ui.select:
        return ui.select(options, value=value, **kwargs).props(
            f'outlined dense options-dense for="{for_id}" dropdown-icon="{icon("chevron-down")}" '
            f'popup-content-class="any-menu"').classes("w-full")

    def _build_setup(self, cfg: dict[str, str], system: str | None) -> None:
        with ui.element("div").classes("any-side-head"):
            text("h2", "Run setup", "any-title")
        with ui.element("div").classes("any-side-body"):
            with ui.element("section").classes("any-section"):
                eyebrow("Target")
                with ui.element("div").classes("any-grid2"):
                    with field("System", "sel-system"):
                        self.system_select = self._select(self.systems, system, "sel-system",
                                                          on_change=self.on_system_change)
                    envs = self.svc.systems.environments(system)
                    env = cfg["envConfig"] if cfg["envConfig"] in envs else (envs[0] if envs else None)
                    with field("Environment", "sel-env"):
                        self.env_select = self._select(envs, env, "sel-env",
                                                       on_change=lambda: self.credentials.refresh())
                tags = _split(cfg["runTags"])
                with field("Tags", "sel-tags"):
                    self.tags_select = self._select(
                        sorted(set(self.catalog.all_tags()) | set(tags), key=str.casefold), tags, "sel-tags",
                        multiple=True, with_input=True, new_value_mode="add-unique", clearable=True,
                        on_change=self._on_tags_change,
                    ).classes("any-multi")
                    self.tags_select.add_slot("selected-item", _chip_slot("tag"))
                    self._sync_tags_placeholder()

            with ui.element("section").classes("any-section"):
                eyebrow("Execution")
                browsers = [b.lower() for b in _split(cfg["browsers"])] or ["chrome"]
                with field("Browsers", "sel-browsers"):
                    self.browsers_select = self._select(
                        BROWSERS + [b for b in browsers if b not in BROWSERS], browsers, "sel-browsers",
                        multiple=True, on_change=self._on_browsers_change,
                    ).classes("any-multi")
                    self.browsers_select.add_slot("selected-item", _chip_slot("browser"))
                    placeholder(self.browsers_select, "Select a browser")
                with field("Parallel workers", "num-workers", hint="pytest-xdist"):
                    with ui.element("div").classes("any-stepper"):
                        self.workers_minus = ui.button(icon=icon("minus"), color=None,
                                                       on_click=lambda: self._step(-1)) \
                            .props('flat unelevated aria-label="Fewer workers"').classes("any-step-btn")
                        self.workers = ui.number(value=1, min=WORKERS_MIN, max=WORKERS_MAX, precision=0,
                                                 on_change=self._on_workers_change) \
                            .props('outlined dense for="num-workers" inputmode="numeric"')
                        self.workers_plus = ui.button(icon=icon("plus"), color=None,
                                                      on_click=lambda: self._step(1)) \
                            .props('flat unelevated aria-label="More workers"').classes("any-step-btn")
                    self._on_workers_change()

                with ui.expansion("Browser & artifacts", icon=icon("sliders-horizontal")) \
                        .props(f'expand-icon="{icon("chevron-down")}" dense-toggle switch-toggle-side=false') \
                        .classes("w-full any-disclosure"):
                    with ui.element("div").classes("any-disclosure-body"):
                        self.headless = ui.switch("Headless", value=_truthy(cfg["headlessMode"]))
                        self.maximized = ui.switch("Maximized window", value=_truthy(cfg["browserConfigMaximized"]))
                        with field("Slow-mo (ms)", "num-slowmo"):
                            self.slowmo = ui.number(value=_int(cfg["slowmo"], 0), min=0, step=50, precision=0) \
                                .props('outlined dense for="num-slowmo"').classes("w-full")
                        waits = ["load", "domcontentloaded", "networkidle", "commit"]
                        with field("Page wait until", "sel-wait"):
                            self.wait_until = self._select(
                                waits, cfg["pageWaitUntil"] if cfg["pageWaitUntil"] in waits else "load", "sel-wait")
                        self.recording = ui.switch("Record video", value=_truthy(cfg["recording"]))
                        self.tracing = ui.switch("Playwright trace", value=_truthy(cfg["tracing"]))
                        self.snapshots = ui.switch("Snapshots", value=_truthy(cfg["snapshots"]))

            with ui.element("section").classes("any-section"):
                eyebrow("Access")
                self.credentials()

        with ui.element("div").classes("any-side-foot"):
            ui.button("Save as defaults", icon=icon("save"), on_click=self.save_defaults, color=None) \
                .props("flat unelevated no-caps").classes("any-btn is-full")
            with ui.element("div").classes("any-env-path"):
                text("b", ".env")
                text("span", str(self.svc.env.env_path))

    @ui.refreshable_method
    def credentials(self) -> None:
        status = self.svc.credential_status(self.system_select.value, self.env_select.value)
        with ui.element("div").classes("any-cred"):
            with ui.element("div").classes("any-cred-tile"):
                ui.icon(icon("key-round")).props('aria-hidden="true"')
            with ui.element("div").classes("flex flex-col gap-1 min-w-0"):
                ui.label("Login credentials").classes("any-cred-title")
                if status is None:
                    ui.label("No login entry in systemData.json for this system/environment.") \
                        .classes("any-cred-line is-warn")
                elif not status:
                    ui.label("No credentials needed.").classes("any-cred-line")
                for item in status or []:
                    ok = item["set"]
                    with ui.element("div").classes("any-cred-key"):
                        ui.icon(icon("circle-check" if ok else "triangle-alert")) \
                            .classes("is-set" if ok else "is-missing").props('aria-hidden="true"')
                        text("code", item["key"])
                        ui.label("set" if ok else "missing - add it to .env")

    def _build_tests(self) -> None:
        with ui.element("section").classes("any-card").props('aria-label="Tests"'):
            with ui.element("div").classes("any-card-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h2", "Tests", "any-title")
                    with ui.element("span").classes("any-count-pill"):
                        self.pill_selected = text("b", "")
                        text("span", " of ")
                        self.pill_total = text("b", "")
                        text("span", " selected")
                with ui.element("div").classes("any-head-right"):
                    ui.switch("Selected only", value=True, on_change=lambda e: self._set_selected_only(e.value))
                    ui.element("div").classes("any-vdivider").props('aria-hidden="true"')
                    icon_button("rotate-cw", "Reload tests", self.reload_catalog)
            self.tests_view()
            with ui.element("div").classes("any-launch"):
                with ui.element("div").classes("any-formula").props('aria-live="polite" data-testid="run-formula"'):
                    self.f_tests = text("b", "")
                    self.f_tests_word = text("span", "")
                    text("span", "×", "op").props('aria-hidden="true"')
                    self.f_browsers = text("b", "")
                    self.f_browsers_word = text("span", "")
                    text("span", "=", "op").props('aria-hidden="true"')
                    with ui.element("span").classes("res"):
                        self.f_runs = text("b", "")
                        self.f_runs_word = text("span", "")
                with ui.element("div").classes("any-launch-right"):
                    self.run_helper = ui.label("Select at least one test and one browser").classes("any-helper")
                    with ui.button(on_click=self.start_run, color=None) \
                            .props('unelevated no-caps data-testid="run-button"') \
                            .classes("any-run") as self.run_button:
                        self.run_icon = ui.icon(icon("play-solid")).props('aria-hidden="true"')
                        self.run_label = ui.label("Run")
                        self.run_badge = ui.label("0").classes("any-run-badge").props('aria-hidden="true"')
        self._update_counts()

    @ui.refreshable_method
    def tests_view(self) -> None:
        matched, chosen = self._selection()
        selected = {id(t) for t in matched}
        checked = {id(t) for t in chosen}
        if self.catalog.errors or any(t.duplicate for t in self.catalog.tests):
            with ui.element("div").classes("any-notes"):
                for err in self.catalog.errors:
                    ui.label(f"Could not read {err}").classes("any-err-line")
                dupes = sorted({t.test_id for t in self.catalog.tests if t.duplicate})
                if dupes:
                    ui.label(f"Duplicate test IDs: {', '.join(dupes)}").classes("any-warn-line")
        rows = []
        for i, t in enumerate(self.catalog.tests):
            if self.show_selected_only and id(t) not in selected:
                continue
            name, code = split_test_id(t.test_id)
            rows.append({"key": i, "run": id(t) in checked, "match": id(t) in selected, "test_id": t.test_id,
                         "name": name, "code": code, "tag_list": t.tags, "tags": ", ".join(t.tags), "file": t.file,
                         "steps": t.steps})
        all_on = bool(matched) and len(chosen) == len(matched)
        some_on = bool(chosen) and not all_on
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="key", pagination=10, columns=[
                {"name": "run", "label": "", "field": "run", "align": "center",
                 "headerClasses": "any-col-check", "classes": "any-col-check"},
                {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
                {"name": "tags", "label": "Tags", "field": "tags", "align": "left"},
                {"name": "file", "label": "File", "field": "file", "align": "left", "sortable": True},
                {"name": "steps", "label": "Steps", "field": "steps", "align": "right", "sortable": True},
            ]).classes("w-full")
            styled_table(table, len(rows))
            table.add_slot("header-cell-run", f'''
                <q-th :props="props" class="any-col-check"><span class="any-check-hit">
                  <input type="checkbox" class="any-check" aria-label="Select all tests matching the tags"
                         title="Select all / none" {"" if matched else "disabled"}
                         :checked="{str(all_on).lower()}" :indeterminate.prop="{str(some_on).lower()}"
                         @change="() => $parent.$emit('toggle_all')">
                </span></q-th>''')
            table.add_slot("body-cell-run", '''
                <q-td :props="props" class="any-col-check"><span class="any-check-hit">
                  <input type="checkbox" class="any-check" :checked="props.value" :disabled="!props.row.match"
                         :title="props.row.match ? '' : 'Not matched by the selected tags'"
                         :aria-label="(props.value ? 'Selected: ' : 'Not selected: ') + props.row.test_id"
                         @change="() => $parent.$emit('toggle_test', props.row.test_id)">
                </span></q-td>''')
            table.on("toggle_test", lambda e: self._toggle_test(e.args))
            table.on("toggle_all", lambda _: self._toggle_all())
            table.add_slot("body-cell-test_id",
                           f'<q-td :props="props">{test_case_template("!props.row.run")}</q-td>')
            table.add_slot("body-cell-tags", '''
                <q-td :props="props"><div class="any-tags">
                  <span v-for="t in props.row.tag_list" :key="t" class="any-tag">{{ t }}</span>
                </div></q-td>''')
            table.add_slot("body-cell-file", f'''
                <q-td :props="props"><span class="any-file">{svg("braces")}{{{{ props.value }}}}</span></q-td>''')
            table.add_slot("body-cell-steps", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
        self._update_counts()

    def _build_run(self) -> None:
        with ui.element("section").classes("any-card").props('aria-labelledby="current-run-title"'):
            with ui.element("div").classes("any-run-head"):
                with ui.element("div").classes("any-run-id"):
                    self.status_tile = ui.element("div").classes("any-tile tone-idle")
                    with self.status_tile:
                        self.status_icon = ui.icon(icon("circle-play")).props('aria-hidden="true"')
                    with ui.element("div").classes("min-w-0"):
                        text("h2", "Current run", "any-eyebrow").props('id="current-run-title"')
                        self.headline = ui.label("Idle").classes("any-headline").props('aria-live="polite"')
                        with ui.element("div").classes("any-meta") as self.meta:
                            with ui.element("span"):
                                text("span", "Run")
                                self.meta_id = text("span", "", "v")
                                icon_button("copy", "Copy run ID", self._copy_run_id, "is-24")
                            with ui.element("span"):
                                text("span", "Target")
                                self.meta_target = text("span", "", "v")
                            with ui.element("span"):
                                text("span", "Tags")
                                self.meta_tags = text("span", "", "v")
                with ui.element("div").classes("any-run-actions"):
                    self.cancel_button = ui.button("Cancel", icon=icon("square-solid"), color=None,
                                                   on_click=self.cancel_run) \
                        .props('unelevated no-caps data-testid="cancel-run"').classes("any-cancel")
                    self.summary_button = ui.button("Report", icon=icon("chart-column"), color=None,
                                                    on_click=self.open_summary) \
                        .props('flat unelevated no-caps data-testid="run-report"').classes("any-btn is-38")
                    self.report_button = ui.button("Allure", icon=icon("external-link"), color=None,
                                                   on_click=self.open_report) \
                        .props('flat unelevated no-caps data-testid="allure-report"').classes("any-btn is-38")
            self.progress_view(None)
            self.message_view("")
            with ui.element("div").classes("any-table-wrap"):
                self.results_table = ui.table(rows=[], row_key="nodeid", pagination=10,
                                              columns=RESULT_COLUMNS).classes("w-full")
                styled_table(self.results_table, 0, wide=True)
                result_slots(self.results_table)
                self.results_table.on("rowClick", lambda e: self._open_details(e.args[1]))
            with ui.element("div").classes("any-console").props('aria-label="Console output"'):
                with ui.element("div").classes("any-console-head"):
                    with ui.element("div").classes("any-console-title"):
                        ui.icon(icon("terminal")).props('aria-hidden="true"')
                        ui.label("Console output")
                        with ui.element("span").classes("any-live") as self.live_badge:
                            ui.element("span").classes("dot").props('aria-hidden="true"')
                            ui.label("live")
                    self.console_button = ui.button("Console log", icon=icon("file-text"), color=None,
                                                    on_click=self.open_console) \
                        .props('flat unelevated no-caps data-testid="console-log"').classes("any-con-btn")
                self.log_view = ConsoleView(max_lines=LOG_LINES_SHOWN)
        self._update_buttons(None)

    @ui.refreshable_method
    def progress_view(self, run: Run | None) -> None:
        with ui.element("div").classes("any-progress"):
            if not run:
                return
            counts = run.counts()
            results = list(run.results.values())
            done = sum(v for k, v in counts.items() if k != "running")
            total = max(run.expected, len(results))
            pending = max(total - len(results), 0)
            placeholder = "queued" if run.active else "not run"
            legend_counts = {**counts, placeholder: pending}
            finished = [r.duration for r in results if r.outcome != "running" and r.duration > 0]
            test_time = sum(r.duration for r in results if r.outcome != "running")
            with ui.element("div").classes("any-summary"):
                with ui.element("div").classes("any-legend"):
                    with ui.element("span"):
                        text("b", f"{done}/{total or '?'}")
                        text("span", "done")
                    for key, swatch, word in LEGEND:
                        n = legend_counts.get(key, 0)
                        if n or key in ("passed", "failed"):
                            with ui.element("span"):
                                ui.element("span").classes(f"any-swatch sw-{swatch}").props('aria-hidden="true"')
                                text("b", str(n))
                                text("span", word)
                    if not counts:
                        ui.label("Starting..." if run.active else "No test results recorded.").classes("any-muted")
                with ui.element("span"):
                    text("span", "Test time ")
                    text("b", f"{test_time:.2f}")
                    text("span", " s")
            segments = [(r.test_id, r.outcome, r.duration) for r in results] + [("", placeholder, 0.0)] * pending
            if not segments:
                return
            avg = (sum(finished) / len(finished)) if finished else 10.0
            dense = len(segments) > DENSE_BAR_RUNS
            described = []
            with ui.element("div").classes("any-bar" + (" is-dense" if dense else "")) as bar:
                for test_id, outcome, seconds in segments:
                    name = split_test_id(test_id)[0] if test_id else ""
                    has_time = outcome not in ("running", "queued", "not run") and seconds > 0
                    label = fmt_seconds(seconds) if has_time else ("running…" if outcome == "running" else
                                                                  placeholder if not test_id else outcome)
                    weight = seconds if has_time else avg
                    seg_tone = tone(outcome) if outcome not in ("queued", "not run") else "idle"
                    tip = f"{test_id or placeholder}: {outcome}" + (f", {fmt_seconds(seconds)}" if has_time else "")
                    described.append(tip)
                    with ui.element("div").classes(f"any-seg seg-{seg_tone}") \
                            .style(f"flex: {max(weight, 0.01):.3f} 1 0").props(f'title="{tip}"'):
                        text("span", name, "n")
                        text("span", label, "d")
            bar.props(f'role="img" data-testid="duration-bar" aria-label="{"; ".join(described)}"')

    @ui.refreshable_method
    def message_view(self, message: str) -> None:
        if not message:
            return
        m = ALLURE_RE.match(message)
        title, body, path = (m.group("title"), m.group("body"), m.group("path")) if m else (message, "", None)
        with ui.element("div").classes("any-callout").props('role="status"'):
            ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
            with ui.element("div").classes("any-callout-body"):
                ui.label(title).classes("any-callout-title")
                if body:
                    with ui.element("div").classes("any-callout-text"):
                        # `backticked` text and ENV_VAR names render as inline code
                        for i, part in enumerate(re.split(r"`([^`]+)`|\b([A-Z][A-Z0-9]*_[A-Z0-9_]+)\b", body)):
                            if part:
                                text("span" if i % 3 == 0 else "code", part)
                if path:
                    with ui.element("div").classes("any-callout-box"):
                        ui.label("Raw results").classes("any-eyebrow")
                        ui.label(path).classes("any-callout-path")
                        copy_button(path, "Copy raw results path", "is-28")

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
            ui.label("No runs yet.").classes("any-muted px-6 pb-5 text-sm")
            return
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="id", pagination=5, columns=[
                {"name": "started", "label": "Started", "field": "started", "align": "left", "sortable": True},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
                {"name": "system", "label": "System", "field": "system", "align": "left"},
                {"name": "env", "label": "Env", "field": "env", "align": "left"},
                {"name": "tags", "label": "Tags", "field": "tags", "align": "left"},
                {"name": "browsers", "label": "Browsers", "field": "browsers", "align": "left"},
                {"name": "result", "label": "Result", "field": "result", "align": "left"},
            ]).classes("w-full is-clickable")
            styled_table(table, len(rows))
            table.classes("is-clickable")
            table.add_slot("body-cell-started", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.add_slot("body-cell-status", f'<q-td :props="props">{status_pill_template()}</q-td>')
            table.add_slot("body-cell-result", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.on("rowClick", lambda e: self.view_run(e.args[1]["id"]))

    def _build_history(self) -> None:
        with ui.element("section").classes("any-card").props('aria-label="Run history"'):
            with ui.element("div").classes("any-history-head"):
                text("h2", "Run history", "any-title")
                ui.label("Click a run to view its results and log.").classes("any-muted")
            self.history()

    # ───────────── presentation helpers ─────────────

    def _selection(self) -> tuple[list, list]:
        """(tests the tags match, those of them still checked in the Tests table)."""
        matched = self.catalog.select(self.tags_select.value or [])
        return matched, [t for t in matched if t.test_id not in self.excluded]

    def _toggle_test(self, test_id: str) -> None:
        if test_id in self.excluded:
            self.excluded.discard(test_id)
        else:
            self.excluded.add(test_id)
        self.tests_view.refresh()

    def _toggle_all(self) -> None:
        matched, chosen = self._selection()
        ids = {t.test_id for t in matched}
        if len(chosen) == len(matched):
            self.excluded |= ids
        else:
            self.excluded -= ids
        self.tests_view.refresh()

    def _update_counts(self) -> None:
        if not hasattr(self, "f_tests"):
            return
        selected = len(self._selection()[1])
        browser_count = max(len(self.browsers_select.value or []), 1)
        self.planned_runs = selected * browser_count
        self.pill_selected.text = str(selected)
        self.pill_total.text = str(len(self.catalog.tests))
        self.f_tests.text = str(selected)
        self.f_tests_word.text = " " + plural(selected, "test")
        self.f_browsers.text = str(browser_count)
        self.f_browsers_word.text = " " + plural(browser_count, "browser")
        self.f_runs.text = str(self.planned_runs)
        self.f_runs_word.text = " " + plural(self.planned_runs, "run")
        self.run_badge.text = str(self.planned_runs)
        if hasattr(self, "cancel_button"):  # the run card is built after the tests card
            self._update_buttons(self._run())

    def _on_tags_change(self) -> None:
        self._sync_tags_placeholder()
        self.tests_view.refresh()

    def _sync_tags_placeholder(self) -> None:
        # Quasar shows an input placeholder even next to chips; only show it while no tag is selected
        if self.tags_select.value:
            self.tags_select.props(remove="placeholder")
        else:
            self.tags_select.props('placeholder="Select tags"')

    def _on_browsers_change(self) -> None:
        self.tests_view.refresh()

    def _step(self, delta: int) -> None:
        current = int(self.workers.value or WORKERS_MIN)
        self.workers.value = min(max(current + delta, WORKERS_MIN), WORKERS_MAX)

    def _on_workers_change(self) -> None:
        value = int(self.workers.value or WORKERS_MIN)
        self.workers_minus.set_enabled(value > WORKERS_MIN)
        self.workers_plus.set_enabled(value < WORKERS_MAX)

    def _open_details(self, row: dict) -> None:
        run = self._run()
        if run:
            open_test_details(run, row)

    def _copy_run_id(self) -> None:
        if self.run_id:
            ui.clipboard.write(self.run_id)
            ui.notify("Copied", type="positive", timeout=1200)

    def _show_idle(self) -> None:
        tile_tone, tile_icon, headline = IDLE_LOOK
        self.status_tile.classes(replace=f"any-tile {tile_tone}")
        self.status_icon.name = icon(tile_icon)
        self.headline.text = headline
        self.meta.set_visibility(False)

    # ───────────── actions ─────────────

    def on_system_change(self) -> None:
        self.excluded.clear()
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
        if not guard("run", "start run"):
            return
        tags = self.tags_select.value or []
        browsers = self.browsers_select.value or ["chrome"]
        matched, selected = self._selection()
        if not selected:
            ui.notify("No test cases match the selected tags." if not matched else "No test cases are checked.",
                      type="warning")
            return
        # All checked -> no ID filter (same payload as before); a subset -> run only those IDs
        test_ids = sorted({t.test_id for t in selected}) if len(selected) < len(matched) else []
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
                           workers=int(self.workers.value or 1), options=self._options(), test_ids=test_ids)
        try:
            run = self.svc.runs.start(config, expected=len(selected) * len(browsers))
        except (ValueError, RuntimeError) as e:
            ui.notify(str(e), type="negative")
            return
        audit("run.start", run.id, system=config.system, env=config.env, tags=config.tags)
        position = self.svc.runs.queue_position(run.id)
        ui.notify(f"Run {run.id} queued (#{position}); it starts when a slot is free" if position
                  else f"Run {run.id} started", type="info" if position else "positive")
        self.view_run(run.id)
        self.history.refresh()

    def cancel_run(self) -> None:
        if not guard("run", "cancel run"):
            return
        if self.run_id and self.svc.runs.cancel(self.run_id):
            audit("run.cancel", self.run_id)
            ui.notify("Cancelling run...", type="info")

    def save_defaults(self) -> None:
        if not guard("settings", "save defaults"):
            return
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
        audit("config.save", ", ".join(sorted(values)))
        ui.notify("Saved as defaults in .env", type="positive")

    def open_summary(self) -> None:
        run = self._run()
        if run and not run.active and self.svc.runs.write_summary(run):
            ui.navigate.to(f"/runs/{run.id}/report.html", new_tab=True)

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
            self.log_view.push(lines[-LOG_LINES_PER_TICK:])

        counts = run.counts()
        signature = (run.status, run.report_status, len(run.results), tuple(sorted(counts.items())))
        if signature == self.last_signature:
            return
        was_active = bool(self.last_signature) and self.last_signature[0] in ACTIVE
        self.last_signature = signature

        tile_tone, tile_icon, headline = RUN_LOOK.get(run.status, IDLE_LOOK)
        self.status_tile.classes(replace=f"any-tile {tile_tone}")
        self.status_icon.name = icon(tile_icon)
        self.headline.text = headline
        self.meta.set_visibility(True)
        self.meta_id.text = run.id
        self.meta_target.text = f"{run.config.system} / {run.config.env}"
        self.meta_tags.text = ", ".join(run.config.tags)
        self.log_view.set_active(run.active)
        self.progress_view.refresh(run)
        # A missing Allure CLI is no longer a problem: the built-in report covers it
        report_problem = run.report_message if run.report_status == "failed" else ""
        self.message_view.refresh(run.error or report_problem)
        rows = result_rows(run)
        self.results_table.rows = rows
        styled_table(self.results_table, len(rows), wide=True)
        self.results_table.update()
        if was_active and not run.active:
            self.history.refresh()
            ui.notify(f"Run {run.id} finished: {run.status}",
                      type="positive" if run.status == "passed" else "warning")

    def _update_buttons(self, run: Run | None) -> None:
        busy = any(r.active for r in self.svc.runs.list())
        # While a run is in progress, Run adds the new one to the queue
        self.run_button.set_enabled(self.planned_runs > 0)
        self.run_helper.set_visibility(self.planned_runs == 0)
        if busy != self._busy:
            self._busy = busy
            self.run_icon.name = icon("list-plus" if busy else "play-solid")
            self.run_label.text = "Queue" if busy else "Run"
        # Cancel only does something while the viewed run is active
        self.cancel_button.set_visibility(bool(run and run.active))
        self.live_badge.set_visibility(bool(run and run.active))
        self.summary_button.set_enabled(bool(run and not run.active))
        self.report_button.set_enabled(bool(run and run.report_status == "ready"))
        self.report_button.set_visibility(self._allure or bool(run and run.report_status == "ready"))
        self.console_button.set_enabled(bool(run))
