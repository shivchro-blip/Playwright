"""Generator tab: edit test cases and page objects (POM JSON) for a system."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Callable

from nicegui import ui

from webapp.services.env_config import FIELDS as ENV_FIELDS
from webapp.services.json_files import ConflictError, PathError
from webapp.services.pom_service import FIELD_TYPES, PAGE_KEYS
from webapp.services.run_manager import RunConfig
from webapp.services.test_case_service import STEP_KINDS
from webapp.state import services

VALUE_KINDS = {"text": "Text", "auto": "Auto", "number": "Number", "json": "JSON"}
KIND_LABELS = {"screen": "Page", "commonAction": "Common action", "testCase": "Reusable test", "API_REQUEST": "API request"}
KIND_COLORS = {"screen": "primary", "commonAction": "teal", "testCase": "purple", "API_REQUEST": "brown"}
TC_META = ("testCaseID", "system", "env", "module", "tags", "description")
SAVE_ERRORS = (ValueError, ConflictError, PathError, OSError)


# ───────────── value helpers ─────────────

def value_kind(value) -> str:
    if value is None:
        return "auto"
    if isinstance(value, bool):
        return "json"
    if isinstance(value, (int, float)):
        return "number"
    return "text" if isinstance(value, str) else "json"


def value_text(value, kind: str) -> str:
    if kind == "auto":
        return ""
    if kind == "json":
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_value(kind: str, raw: str):
    raw = raw if raw is not None else ""
    if kind == "auto":
        return None
    if kind == "number":
        number = float(raw)
        return int(number) if number.is_integer() and "." not in raw else number
    if kind == "json":
        return json.loads(raw)
    return raw


async def confirm(message: str, ok: str = "OK", color: str = "primary") -> bool:
    with ui.dialog() as dialog, ui.card():
        ui.label(message)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(ok, color=color, on_click=lambda: dialog.submit(True))
    result = await dialog
    dialog.delete()
    return bool(result)


# ───────────── test case editor ─────────────

@dataclass
class FormRow:
    key: str
    kind: str
    raw: str


@dataclass
class StepModel:
    kind: str
    target: str
    rows: list[FormRow] = field(default_factory=list)
    had_form: bool = False
    extra: dict = field(default_factory=dict)  # other step keys (override, loop, ...) kept as-is

    @classmethod
    def from_step(cls, step: dict) -> "StepModel":
        kind = next((k for k in STEP_KINDS if k in step), "screen")
        form = step.get("formData")
        rows = [FormRow(k, value_kind(v), value_text(v, value_kind(v))) for k, v in (form or {}).items()]
        extra = {k: v for k, v in step.items() if k not in (kind, "formData")}
        return cls(kind, str(step.get(kind, "")), rows, isinstance(form, dict), extra)

    def to_step(self, n: int) -> dict:
        step: dict = {self.kind: self.target}
        step.update(self.extra)
        if self.kind == "screen" and (self.rows or self.had_form):
            form = {}
            for row in self.rows:
                key = row.key.strip()
                if not key:
                    raise ValueError(f"Step {n}: a form field has no name.")
                if key in form:
                    raise ValueError(f"Step {n}: field '{key}' is listed twice - add '|' to repeat it (e.g. '{key}|').")
                try:
                    form[key] = parse_value(row.kind, row.raw)
                except ValueError as e:
                    raise ValueError(f"Step {n}, field '{key}': invalid {row.kind} value ({e}).") from e
            step["formData"] = form
        return step


class TestEditor:
    def __init__(self, gen: "GeneratorPanel"):
        self.gen = gen
        self.svc = services()
        self.file: str | None = None
        self.index: int | None = None
        self.hash: str | None = None
        self.meta: dict = {}
        self.orig_keys: set[str] = set()
        self.extra: dict = {}
        self.steps: list[StepModel] = []
        self.dirty = False
        self.warnings: list[str] = []
        self.json_mode = False
        self._screens: list[str] = []
        self._common: list[str] = []
        self._test_ids: list[str] = []

        with ui.splitter(value=28, limits=(18, 45)).classes("w-full") as splitter:
            with splitter.before:
                with ui.column().classes("w-full pr-3 gap-2"):
                    with ui.row().classes("w-full items-center gap-1 no-wrap"):
                        self.search = ui.input(placeholder="Search tests").props("dense clearable").classes("flex-1")
                        ui.button(icon="add", on_click=self.new_test_dialog).props("flat round dense") \
                            .tooltip("New test case")
                        ui.button(icon="refresh", on_click=self.reload_list).props("flat round dense") \
                            .tooltip("Reload")
                    self.list_view()
            with splitter.after:
                with ui.column().classes("w-full pl-3 gap-3"):
                    self.editor()

    def load_refs(self) -> None:
        """Pickers for step targets (pages, common actions, reusable test IDs)."""
        system = self.gen.system
        self._screens = sorted(self.svc.pom.screen_index(system), key=str.casefold) if system else []
        self._common = list(self.svc.tests.common_actions())
        self._test_ids = self.svc.tests.all_test_ids()

    # ───── list ─────

    @ui.refreshable_method
    def list_view(self) -> None:
        system = self.gen.system
        if not system:
            ui.label("Pick a system.").classes("text-sm text-grey-7")
            return
        refs, errors = self.svc.tests.list_tests(system)
        for err in errors:
            ui.label(f"Could not read {err}").classes("text-xs text-negative")
        rows = [{"key": f"{r.file}#{r.index}", "file": r.file, "index": r.index, "test_id": r.test_id,
                 "tags": ", ".join(r.tags), "steps": r.steps, "dup": r.duplicate} for r in refs]
        table = ui.table(rows=rows, row_key="key", pagination=15, columns=[
            {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
            {"name": "file", "label": "File", "field": "file", "align": "left", "sortable": True},
            {"name": "steps", "label": "Steps", "field": "steps"},
        ]).classes("w-full cursor-pointer").props("dense flat")
        table.add_slot("body-cell-test_id", r'''
            <q-td :props="props">
              <div class="font-medium">{{ props.value }}
                <q-icon v-if="props.row.dup" name="content_copy" color="orange" size="xs">
                  <q-tooltip>Duplicate test ID</q-tooltip></q-icon></div>
              <div class="text-xs text-grey-7">{{ props.row.tags }}</div>
            </q-td>''')
        self.search.bind_value(table, "filter")
        table.on("rowClick", lambda e: self.open(e.args[1]["file"], e.args[1]["index"]))
        if not rows:
            ui.label("No test cases for this system yet - use + to create one.").classes("text-sm text-grey-7")

    def reload_list(self) -> None:
        self.load_refs()
        self.list_view.refresh()

    # ───── load / build ─────

    async def open(self, file: str, index: int) -> None:
        if self.dirty and not await confirm("Discard unsaved changes to this test case?", "Discard", "negative"):
            return
        try:
            tc, sha = self.svc.tests.get(self.gen.system, file, index)
        except SAVE_ERRORS as e:
            ui.notify(str(e), type="negative")
            return
        self.file, self.index, self.hash = file, index, sha
        self._set_model(tc)
        self.validate(notify=False)

    def _set_model(self, tc: dict) -> None:
        self.orig_keys = set(tc)
        self.meta = {k: copy.deepcopy(tc.get(k)) for k in TC_META}
        self.meta["tags"] = list(self.meta.get("tags") or [])
        self.extra = {k: copy.deepcopy(v) for k, v in tc.items() if k not in TC_META and k != "steps"}
        self.steps = [StepModel.from_step(s) for s in tc.get("steps") or [] if isinstance(s, dict)]
        self.dirty = False
        self.editor.refresh()

    def build(self) -> dict:
        tc: dict = {}
        for key in TC_META:
            value = self.meta.get(key)
            if value is None or (value in ("", []) and key not in self.orig_keys and key != "testCaseID"):
                continue  # don't add empty optional keys the file never had
            tc[key] = value
        tc["steps"] = [s.to_step(n) for n, s in enumerate(self.steps, 1)]
        tc.update(self.extra)
        return tc

    def touch(self) -> None:
        if not self.dirty:
            self.dirty = True
            self.dirty_badge.set_visibility(True)

    # ───── editor ─────

    @ui.refreshable_method
    def editor(self) -> None:
        if self.file is None:
            ui.label("Select a test case on the left, or create a new one with +.").classes("text-grey-7 mt-8")
            return
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(self.meta.get("testCaseID") or "(no id)").classes("text-lg font-medium")
            ui.label(f"{self.file} #{self.index}").classes("text-xs font-mono text-grey-7")
            self.dirty_badge = ui.badge("unsaved", color="orange")
            self.dirty_badge.set_visibility(self.dirty)
            ui.space()
            ui.toggle({False: "Form", True: "JSON"}, value=self.json_mode, on_change=self.toggle_json) \
                .props("dense no-caps")
            ui.button("Check", icon="rule", on_click=lambda: self.validate(True)).props("flat no-caps")
            ui.button("Revert", icon="undo", on_click=self.revert).props("flat no-caps")
            ui.button("Copy", icon="content_copy", on_click=self.copy_dialog).props("flat no-caps") \
                .tooltip("Save as a new test case")
            ui.button(icon="delete", color="negative", on_click=self.delete).props("flat round").tooltip("Delete")
            ui.button("Run", icon="play_arrow", on_click=self.run_this).props("outline no-caps") \
                .tooltip("Run only this test case")
            ui.button("Save", icon="save", on_click=self.save).props("no-caps")
        self.validation_view()
        if self.json_mode:
            self.json_editor()
        else:
            self.meta_form()
            self.steps_view()

    @ui.refreshable_method
    def validation_view(self) -> None:
        if self.warnings:
            with ui.expansion(f"{len(self.warnings)} warning(s)", icon="warning").classes("w-full bg-orange-1"):
                for w in self.warnings:
                    ui.label(w).classes("text-sm")

    def meta_form(self) -> None:
        def bind(key):
            def handler(e):
                self.meta[key] = e.value
                self.touch()
            return handler

        with ui.card().classes("w-full"):
            with ui.row().classes("w-full gap-3"):
                ui.input("Test case ID", value=self.meta.get("testCaseID") or "", on_change=bind("testCaseID")) \
                    .classes("flex-1 min-w-48")
                ui.select(self.gen.all_systems(), label="System", value=self.meta.get("system"),
                          with_input=True, new_value_mode="add-unique", on_change=bind("system")).classes("w-36")
                ui.input("Env", value=self.meta.get("env") or "", on_change=bind("env")).classes("w-28")
                ui.input("Module", value=self.meta.get("module") or "", on_change=bind("module")).classes("w-36")
            ui.input_chips("Tags", value=list(self.meta.get("tags") or []), new_value_mode="add-unique",
                           on_change=bind("tags")).classes("w-full")
            ui.textarea("Description", value=self.meta.get("description") or "", on_change=bind("description")) \
                .props("autogrow").classes("w-full")

    @ui.refreshable_method
    def steps_view(self) -> None:
        with ui.row().classes("w-full items-center"):
            ui.label(f"Steps ({len(self.steps)})").classes("text-md font-medium")
            ui.space()
            for kind, label in (("screen", "Page"), ("commonAction", "Common action"), ("testCase", "Reusable test")):
                ui.button(f"+ {label}", on_click=lambda k=kind: self.add_step(k)).props("outline dense no-caps")
        if not self.steps:
            ui.label("No steps yet.").classes("text-sm text-grey-7")
        for i, step in enumerate(self.steps):
            self.step_card(i, step)

    def step_card(self, i: int, step: StepModel) -> None:
        options = {"screen": self._screens, "commonAction": self._common, "testCase": self._test_ids}.get(step.kind, [])
        options = options + ([step.target] if step.target and step.target not in options else [])

        def set_target(e):
            step.target = e.value or ""
            self.touch()
            if step.kind == "screen":
                self.steps_view.refresh()  # field picker depends on the page

        with ui.card().classes("w-full").props("flat bordered"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.badge(str(i + 1), color="grey-7")
                ui.badge(KIND_LABELS.get(step.kind, step.kind), color=KIND_COLORS.get(step.kind, "grey"))
                ui.select(options, value=step.target or None, with_input=True, new_value_mode="add-unique",
                          on_change=set_target).props("dense").classes("flex-1")
                ui.button(icon="arrow_upward", on_click=lambda: self.move(i, -1)).props("flat round dense") \
                    .set_enabled(i > 0)
                ui.button(icon="arrow_downward", on_click=lambda: self.move(i, 1)).props("flat round dense") \
                    .set_enabled(i < len(self.steps) - 1)
                ui.button(icon="content_copy", on_click=lambda: self.duplicate(i)).props("flat round dense") \
                    .tooltip("Duplicate step")
                ui.button(icon="close", color="negative", on_click=lambda: self.remove(i)).props("flat round dense")
            if step.kind == "commonAction":
                def set_override(e):
                    if e.value:
                        step.extra["override"] = e.value
                    else:
                        step.extra.pop("override", None)
                    self.touch()
                ui.input("Override (screen|field|value)", value=step.extra.get("override", ""),
                         on_change=set_override).props("dense").classes("w-full")
            others = {k: v for k, v in step.extra.items() if k != "override"}
            if others:
                ui.label("Other step keys (edit in JSON view): " + ", ".join(others)).classes("text-xs text-grey-7")
            if step.kind == "screen":
                self.form_rows(step)

    def form_rows(self, step: StepModel) -> None:
        system = self.meta.get("system") or self.gen.system
        fields = self.svc.pom.field_names(system, step.target) if step.target and system else []
        custom = self.svc.pom.custom_fields()
        for row in step.rows:
            key_options = sorted(set(fields) | ({row.key} if row.key else set()), key=str.casefold) + \
                [c for c in custom if c != row.key]

            def set_key(e, r=row):
                r.key = e.value or ""
                self.touch()

            def set_kind(e, r=row):
                r.kind = e.value
                if r.kind == "auto":
                    r.raw = ""
                self.touch()
                self.steps_view.refresh()

            def set_raw(e, r=row):
                r.raw = e.value or ""
                self.touch()

            with ui.row().classes("w-full items-center gap-2 no-wrap pl-8"):
                ui.select(key_options, value=row.key or None, with_input=True, new_value_mode="add-unique",
                          on_change=set_key).props("dense").classes("w-64")
                ui.select(VALUE_KINDS, value=row.kind, on_change=set_kind).props("dense").classes("w-28")
                if row.kind == "auto":
                    ui.label("auto-generated (null)").classes("flex-1 text-sm text-grey-7 italic")
                else:
                    ui.input(value=row.raw, on_change=set_raw,
                             placeholder={"json": '{"type": "confirmtext"}', "number": "0"}.get(row.kind, "value or click")) \
                        .props("dense").classes("flex-1")
                ui.button(icon="close", on_click=lambda r=row: self.remove_row(step, r)).props("flat round dense")
        with ui.row().classes("pl-8 gap-2"):
            ui.button("+ Field", on_click=lambda: self.add_row(step)).props("flat dense no-caps")
            if fields:
                ui.button(f"+ All {len(fields)} page fields", on_click=lambda: self.add_all_rows(step, fields)) \
                    .props("flat dense no-caps")

    def json_editor(self) -> None:
        try:
            text = json.dumps(self.build(), indent=2, ensure_ascii=False)
        except ValueError as e:
            text = f"// Fix form errors first: {e}"
        self.code = ui.codemirror(text, language="JSON", line_wrapping=True).classes("w-full h-[60vh]")
        self.code.on_value_change(lambda: self.touch())

    # ───── step / row actions ─────

    def add_step(self, kind: str) -> None:
        self.steps.append(StepModel(kind, ""))
        self.touch()
        self.steps_view.refresh()

    def move(self, i: int, delta: int) -> None:
        j = i + delta
        self.steps[i], self.steps[j] = self.steps[j], self.steps[i]
        self.touch()
        self.steps_view.refresh()

    def duplicate(self, i: int) -> None:
        self.steps.insert(i + 1, copy.deepcopy(self.steps[i]))
        self.touch()
        self.steps_view.refresh()

    def remove(self, i: int) -> None:
        del self.steps[i]
        self.touch()
        self.steps_view.refresh()

    def add_row(self, step: StepModel) -> None:
        step.rows.append(FormRow("", "text", ""))
        self.touch()
        self.steps_view.refresh()

    def add_all_rows(self, step: StepModel, fields: list[str]) -> None:
        present = {r.key for r in step.rows}
        step.rows += [FormRow(f, "auto", "") for f in fields if f not in present]
        self.touch()
        self.steps_view.refresh()

    def remove_row(self, step: StepModel, row: FormRow) -> None:
        step.rows.remove(row)
        self.touch()
        self.steps_view.refresh()

    # ───── commands ─────

    def _current(self) -> dict | None:
        """Test case from whichever view is active; notifies and returns None on errors."""
        try:
            if self.json_mode:
                tc = json.loads(self.code.value)
                if not isinstance(tc, dict):
                    raise ValueError("JSON must be an object.")
                return tc
            return self.build()
        except ValueError as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return None

    def toggle_json(self, e) -> None:
        if e.value == self.json_mode:
            return
        tc = self._current()
        if tc is None:
            e.sender.value = self.json_mode
            return
        dirty = self.dirty
        self.json_mode = e.value
        self._set_model(tc)
        if dirty:
            self.touch()

    def validate(self, notify: bool = True) -> bool:
        tc = self._current()
        if tc is None:
            return False
        result = self.svc.tests.validate(self.gen.system, tc)
        self.warnings = result.errors + result.warnings
        self.validation_view.refresh()
        if notify:
            if result.errors:
                ui.notify(f"{len(result.errors)} error(s)", type="negative")
            else:
                ui.notify("No problems found" if not result.warnings else f"{len(result.warnings)} warning(s)",
                          type="positive" if not result.warnings else "warning")
        return not result.errors

    def save(self) -> None:
        tc = self._current()
        if tc is None:
            return
        try:
            self.index, self.hash = self.svc.tests.save(self.gen.system, self.file, self.index, tc, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(f"Not saved: {e}", type="negative", multi_line=True, timeout=8000)
            return
        self._set_model(tc)
        self.validate(notify=False)
        self.list_view.refresh()
        ui.notify(f"Saved {tc.get('testCaseID')} to {self.file}", type="positive")

    async def run_this(self) -> None:
        """Run just this test case with the saved defaults (.env) for browser options."""
        if self.dirty:
            if not await confirm("Save changes before running?", "Save and run"):
                return
            self.save()
            if self.dirty:  # save failed
                return
        test_id = str(self.meta.get("testCaseID") or "")
        tags = list(self.meta.get("tags") or [])
        if not tags:
            ui.notify("Add at least one tag - the framework only selects tagged tests.", type="warning")
            return
        system = self.gen.system or ""
        env = self.meta.get("env") or next(iter(self.svc.systems.environments(system)), "")
        defaults = self.svc.env.load()
        options = {f.key: defaults[f.key] for f in ENV_FIELDS if f.group in ("Browser", "Artifacts")
                   and f.key != "del_allure"}
        browsers = [b.strip() for b in defaults.get("browsers", "").split(",") if b.strip()] or ["chrome"]
        try:
            run = self.svc.runs.start(RunConfig(system=system, env=env, tags=tags, browsers=browsers,
                                                options=options, test_ids=[test_id]), expected=len(browsers))
        except (ValueError, RuntimeError) as e:
            ui.notify(str(e), type="negative")
            return
        ui.notify(f"Running {test_id} ({run.id})", type="positive")
        if self.gen.on_run:
            self.gen.on_run(run.id)

    async def revert(self) -> None:
        if self.file is None:
            return
        self.dirty = False
        await self.open(self.file, self.index)

    async def delete(self) -> None:
        if self.file is None or not await confirm(
                f"Delete test case '{self.meta.get('testCaseID')}' from {self.file}?", "Delete", "negative"):
            return
        try:
            self.svc.tests.delete(self.gen.system, self.file, self.index, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(str(e), type="negative")
            return
        ui.notify("Deleted", type="positive")
        self.file = self.index = self.hash = None
        self.dirty = False
        self.editor.refresh()
        self.list_view.refresh()

    def _file_dialog(self, title: str, default_id: str, on_ok) -> None:
        files = self.svc.tests.files(self.gen.system) if self.gen.system else []
        new_label = "+ New file..."
        with ui.dialog() as dialog, ui.card().classes("w-[28rem]"):
            ui.label(title).classes("text-lg font-medium")
            test_id = ui.input("Test case ID", value=default_id).classes("w-full")
            file_sel = ui.select(files + [new_label], label="File", value=self.file if self.file in files
                                 else (files[0] if files else new_label)).classes("w-full")
            new_name = ui.input("New file name", placeholder="MyTests.json").classes("w-full")
            new_name.bind_visibility_from(file_sel, "value", value=new_label)

            def ok():
                tid = (test_id.value or "").strip()
                if not tid:
                    ui.notify("Test case ID is required", type="warning")
                    return
                try:
                    target = (self.svc.tests.normalize_file_name(new_name.value or "")
                              if file_sel.value == new_label else file_sel.value)
                except PathError as e:
                    ui.notify(str(e), type="warning")
                    return
                dialog.close()
                on_ok(tid, target)

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Create", on_click=ok)
        dialog.open()

    async def new_test_dialog(self) -> None:
        if not self.gen.system:
            ui.notify("Pick a system first", type="warning")
            return
        if self.dirty and not await confirm("Discard unsaved changes to this test case?", "Discard", "negative"):
            return
        self.dirty = False
        system = self.gen.system
        self._file_dialog("New test case", f"TC-{system}-NEW", lambda tid, rel: self._create(rel, {
            "testCaseID": tid, "system": system, "env": "dev", "module": "", "tags": [system], "steps": []}))

    def copy_dialog(self) -> None:
        tc = self._current()
        if tc is None:
            return
        self._file_dialog("Copy test case", f"{tc.get('testCaseID', '')}-COPY",
                          lambda tid, rel: self._create(rel, {**tc, "testCaseID": tid}))

    def _create(self, rel: str, tc: dict) -> None:
        try:
            index, sha = self.svc.tests.save(self.gen.system, rel, None, tc, None)
        except SAVE_ERRORS as e:
            ui.notify(f"Not created: {e}", type="negative", multi_line=True)
            return
        self.file, self.index, self.hash = rel, index, sha
        self.json_mode = False
        self._set_model(tc)
        self.validate(notify=False)
        self.list_view.refresh()
        ui.notify(f"Created {tc['testCaseID']} in {rel}", type="positive")


# ───────────── page (POM) editor ─────────────

@dataclass
class FieldRow:
    name: str
    cfg: dict


class PageEditor:
    def __init__(self, gen: "GeneratorPanel"):
        self.gen = gen
        self.svc = services()
        self.rel: str | None = None
        self.hash: str | None = None
        self.page: dict = {}
        self.rows: list[FieldRow] = []
        self.dirty = False
        self.json_mode = False
        self.field_filter = ""

        with ui.splitter(value=28, limits=(18, 45)).classes("w-full") as splitter:
            with splitter.before:
                with ui.column().classes("w-full pr-3 gap-2"):
                    with ui.row().classes("w-full items-center gap-1 no-wrap"):
                        self.search = ui.input(placeholder="Search pages").props("dense clearable").classes("flex-1")
                        ui.button(icon="add", on_click=self.new_page_dialog).props("flat round dense") \
                            .tooltip("New page")
                        ui.button(icon="refresh", on_click=lambda: self.list_view.refresh()) \
                            .props("flat round dense").tooltip("Reload")
                    self.list_view()
            with splitter.after:
                with ui.column().classes("w-full pl-3 gap-3"):
                    self.editor()

    @ui.refreshable_method
    def list_view(self) -> None:
        system = self.gen.system
        if not system:
            ui.label("Pick a system.").classes("text-sm text-grey-7")
            return
        refs = self.svc.pom.list_pages(system)
        rows = [{"rel": r.rel, "name": r.name, "folder": r.rel.rpartition("/")[0], "fields": r.fields,
                 "dup": r.duplicate} for r in refs]
        table = ui.table(rows=rows, row_key="rel", pagination=15, columns=[
            {"name": "name", "label": "Page", "field": "name", "align": "left", "sortable": True},
            {"name": "folder", "label": "Folder", "field": "folder", "align": "left", "sortable": True},
            {"name": "fields", "label": "Fields", "field": "fields", "sortable": True},
        ]).classes("w-full cursor-pointer").props("dense flat")
        table.add_slot("body-cell-name", r'''
            <q-td :props="props">{{ props.value }}
              <q-icon v-if="props.row.dup" name="content_copy" color="orange" size="xs">
                <q-tooltip>Another page has this name - the last one loaded wins</q-tooltip></q-icon>
            </q-td>''')
        self.search.bind_value(table, "filter")
        table.on("rowClick", lambda e: self.open(e.args[1]["rel"]))
        if not rows:
            ui.label("No pages for this system yet - use + to create one.").classes("text-sm text-grey-7")

    async def open(self, rel: str) -> None:
        if self.dirty and not await confirm("Discard unsaved changes to this page?", "Discard", "negative"):
            return
        try:
            data, sha = self.svc.pom.load(self.gen.system, rel)
        except SAVE_ERRORS as e:
            ui.notify(str(e), type="negative")
            return
        self.rel, self.hash = rel, sha
        self._set_model(data)

    def _set_model(self, data: dict) -> None:
        self.page = {k: copy.deepcopy(v) for k, v in data.items() if k != "fields"}
        self.rows = [FieldRow(k, copy.deepcopy(v) if isinstance(v, dict) else {"locator": str(v)})
                     for k, v in (data.get("fields") or {}).items()]
        self.dirty = False
        self.editor.refresh()

    def build(self) -> dict:
        fields = {}
        for row in self.rows:
            name = row.name.strip()
            if not name:
                raise ValueError("A field has no name.")
            if name in fields:
                raise ValueError(f"Field '{name}' is defined twice.")
            fields[name] = row.cfg
        page = {k: v for k, v in self.page.items() if v not in ("", None, [])}
        page["fields"] = fields
        return page

    def touch(self) -> None:
        if not self.dirty:
            self.dirty = True
            self.dirty_badge.set_visibility(True)

    @ui.refreshable_method
    def editor(self) -> None:
        if self.rel is None:
            ui.label("Select a page on the left, or create a new one with +.").classes("text-grey-7 mt-8")
            return
        name = self.rel.rpartition("/")[2][:-5]
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(name).classes("text-lg font-medium")
            ui.label(f"Pages/{self.gen.system}/{self.rel}").classes("text-xs font-mono text-grey-7")
            self.dirty_badge = ui.badge("unsaved", color="orange")
            self.dirty_badge.set_visibility(self.dirty)
            ui.space()
            ui.toggle({False: "Form", True: "JSON"}, value=self.json_mode, on_change=self.toggle_json) \
                .props("dense no-caps")
            ui.button("Revert", icon="undo", on_click=self.revert).props("flat no-caps")
            ui.button("Save", icon="save", on_click=self.save).props("no-caps")
        if self.json_mode:
            try:
                text = json.dumps(self.build(), indent=2, ensure_ascii=False)
            except ValueError as e:
                text = f"// Fix form errors first: {e}"
            self.code = ui.codemirror(text, language="JSON", line_wrapping=True).classes("w-full h-[65vh]")
            self.code.on_value_change(lambda: self.touch())
            return
        self.page_form()
        self.fields_view()

    def page_form(self) -> None:
        def bind(key):
            def handler(e):
                self.page[key] = e.value
                self.touch()
            return handler

        with ui.expansion("Page settings (URL, title and error checks)", icon="settings").classes("w-full") \
                .props("dense"):
            with ui.row().classes("w-full gap-3"):
                ui.input("path (appended to base_url)", value=self.page.get("path") or "",
                         on_change=bind("path")).classes("flex-1")
                ui.input("fullpath (absolute URL)", value=self.page.get("fullpath") or "",
                         on_change=bind("fullpath")).classes("flex-1")
            with ui.row().classes("w-full gap-3"):
                ui.input("titleLocator", value=self.page.get("titleLocator") or "",
                         on_change=bind("titleLocator")).classes("flex-1")
                ui.input("titleContains", value=self.page.get("titleContains") or "",
                         on_change=bind("titleContains")).classes("flex-1")
            with ui.row().classes("w-full gap-3"):
                for key in ("errorLocators", "errorContains"):
                    value = self.page.get(key)
                    ui.input_chips(key, value=list(value) if isinstance(value, list) else [],
                                   new_value_mode="add-unique", on_change=bind(key)).classes("flex-1")
            others = [k for k in self.page if k not in PAGE_KEYS]
            if others:
                ui.label("Other page keys (edit in JSON view): " + ", ".join(others)).classes("text-xs text-grey-7")

    @ui.refreshable_method
    def fields_view(self) -> None:
        autogen = self.svc.pom.autogen_rules()
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(f"Fields ({len(self.rows)})").classes("text-md font-medium")
            ui.input(placeholder="Filter fields", value=self.field_filter,
                     on_change=self._set_filter).props("dense clearable debounce=300").classes("w-56")
            ui.space()
            ui.button("+ Field", on_click=self.add_field).props("outline dense no-caps")
        with ui.row().classes("w-full gap-2 no-wrap text-xs text-grey-7 pl-1"):
            for label, width in (("Name", "w-48"), ("Type", "w-36"), ("Locator", "flex-1"), ("Index", "w-20"),
                                 ("autoGen", "w-44"), ("", "w-20")):
                ui.label(label).classes(width)
        needle = (self.field_filter or "").casefold()
        shown = [r for r in self.rows if not needle or needle in r.name.casefold()
                 or needle in str(r.cfg.get("locator", "")).casefold()]
        for row in shown:
            self.field_row(row, autogen)
        if needle and len(shown) < len(self.rows):
            ui.label(f"{len(self.rows) - len(shown)} field(s) hidden by filter").classes("text-xs text-grey-7")

    def _set_filter(self, e) -> None:
        self.field_filter = e.value or ""
        self.fields_view.refresh()

    def field_row(self, row: FieldRow, autogen: list[str]) -> None:
        def set_name(e):
            row.name = e.value or ""
            self.touch()

        def set_cfg(key, cast=None):
            def handler(e):
                value = e.value
                if value in ("", None):
                    row.cfg.pop(key, None)
                else:
                    row.cfg[key] = cast(value) if cast else value
                self.touch()
            return handler

        types = FIELD_TYPES + ([row.cfg["type"]] if row.cfg.get("type") and row.cfg["type"] not in FIELD_TYPES else [])
        gen_key = "autogen" if "autogen" in row.cfg and "autoGen" not in row.cfg else "autoGen"
        gens = autogen + ([row.cfg[gen_key]] if row.cfg.get(gen_key) and row.cfg[gen_key] not in autogen else [])
        extra = [k for k in row.cfg if k not in ("type", "locator", "index", gen_key)]
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.input(value=row.name, on_change=set_name).props("dense").classes("w-48")
            ui.select(types, value=row.cfg.get("type"), with_input=True, new_value_mode="add-unique",
                      on_change=set_cfg("type")).props("dense").classes("w-36")
            ui.input(value=str(row.cfg.get("locator", "")), on_change=set_cfg("locator")) \
                .props("dense").classes("flex-1 font-mono")
            ui.number(value=row.cfg.get("index"), precision=0, on_change=set_cfg("index", int)) \
                .props("dense").classes("w-20")
            ui.select(gens, value=row.cfg.get(gen_key), with_input=True, clearable=True,
                      on_change=set_cfg(gen_key)).props("dense").classes("w-44")
            with ui.row().classes("w-20 gap-0 no-wrap"):
                more = ui.button(icon="data_object", on_click=lambda: self.field_dialog(row)) \
                    .props("flat round dense").tooltip("All keys: " + (", ".join(extra) if extra else "none"))
                if extra:
                    more.props("color=primary")
                ui.button(icon="close", color="negative", on_click=lambda: self.remove_field(row)) \
                    .props("flat round dense")

    def field_dialog(self, row: FieldRow) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-[36rem]"):
            ui.label(f"Field '{row.name}' - all keys").classes("text-lg font-medium")
            ui.label("role, name, nested, tableId, waitForNewTab, focus, placeholder, label, text, contains, ...") \
                .classes("text-xs text-grey-7")
            code = ui.codemirror(json.dumps(row.cfg, indent=2, ensure_ascii=False), language="JSON") \
                .classes("w-full h-72")

            def apply():
                try:
                    cfg = json.loads(code.value)
                    if not isinstance(cfg, dict):
                        raise ValueError("must be a JSON object")
                except ValueError as e:
                    ui.notify(f"Invalid JSON: {e}", type="negative")
                    return
                row.cfg = cfg
                self.touch()
                dialog.close()
                self.fields_view.refresh()

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Apply", on_click=apply)
        dialog.open()

    def add_field(self) -> None:
        self.rows.append(FieldRow("", {"type": "text", "locator": ""}))
        self.field_filter = ""
        self.touch()
        self.fields_view.refresh()

    def remove_field(self, row: FieldRow) -> None:
        self.rows.remove(row)
        self.touch()
        self.fields_view.refresh()

    def _current(self) -> dict | None:
        try:
            data = json.loads(self.code.value) if self.json_mode else self.build()
            self.svc.pom.validate(data)
            return data
        except ValueError as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return None

    def toggle_json(self, e) -> None:
        if e.value == self.json_mode:
            return
        data = self._current()
        if data is None:
            e.sender.value = self.json_mode
            return
        dirty = self.dirty
        self.json_mode = e.value
        self._set_model(data)
        if dirty:
            self.touch()

    def save(self) -> None:
        data = self._current()
        if data is None:
            return
        try:
            self.hash = self.svc.pom.save(self.gen.system, self.rel, data, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(f"Not saved: {e}", type="negative", multi_line=True, timeout=8000)
            return
        self._set_model(data)
        self.list_view.refresh()
        self.gen.tests.load_refs()
        ui.notify(f"Saved {self.rel}", type="positive")

    async def revert(self) -> None:
        if self.rel is not None:
            self.dirty = False
            await self.open(self.rel)

    async def new_page_dialog(self) -> None:
        system = self.gen.system
        if not system:
            ui.notify("Pick a system first", type="warning")
            return
        if self.dirty and not await confirm("Discard unsaved changes to this page?", "Discard", "negative"):
            return
        folders = sorted({r.rel.rpartition("/")[0] for r in self.svc.pom.list_pages(system)} - {""}, key=str.casefold)
        with ui.dialog() as dialog, ui.card().classes("w-[28rem]"):
            ui.label(f"New page in {system}").classes("text-lg font-medium")
            folder = ui.select(folders, label="Folder (optional - type a new one)", with_input=True,
                               new_value_mode="add-unique", clearable=True).classes("w-full")
            name = ui.input("Page name", placeholder="loginPage").classes("w-full")

            def ok():
                try:
                    rel = self.svc.pom.create(system, folder.value or "", name.value or "")
                    data, sha = self.svc.pom.load(system, rel)
                except SAVE_ERRORS as e:
                    ui.notify(str(e), type="warning")
                    return
                dialog.close()
                self.rel, self.hash = rel, sha
                self.json_mode = False
                self._set_model(data)
                self.list_view.refresh()
                self.gen.tests.load_refs()
                ui.notify(f"Created {rel}", type="positive")

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Create", on_click=ok)
        dialog.open()


# ───────────── tab ─────────────

class GeneratorPanel:
    def __init__(self, on_run: Callable[[str], None] | None = None):
        self.svc = services()
        self.on_run = on_run  # shell callback: follow a started run on the Control tab
        systems = self.all_systems()
        default = self.svc.env.load().get("systemConfig")
        self.system: str | None = default if default in systems else (systems[0] if systems else None)
        with ui.row().classes("w-full items-center gap-3"):
            ui.select(systems, label="System", value=self.system, on_change=self.on_system) \
                .props("dense").classes("w-48")
            ui.label("Edits are saved straight to the workspace JSON files.").classes("text-xs text-grey-7")
        with ui.tabs().props("dense align=left no-caps").classes("w-full") as tabs:
            tests_tab = ui.tab("Test cases", icon="checklist")
            pages_tab = ui.tab("Pages", icon="web")
        with ui.tab_panels(tabs, value=tests_tab).classes("w-full bg-transparent"):
            with ui.tab_panel(tests_tab).classes("p-0 pt-2"):
                self.tests = TestEditor(self)
            with ui.tab_panel(pages_tab).classes("p-0 pt-2"):
                self.pages = PageEditor(self)
        self.tests.load_refs()

    def all_systems(self) -> list[str]:
        return sorted(set(self.svc.pom.systems()) | set(self.svc.tests.systems()) | set(self.svc.systems.systems()))

    async def on_system(self, e) -> None:
        if e.value == self.system:
            return
        if (self.tests.dirty or self.pages.dirty) and not await confirm(
                "Discard unsaved changes and switch system?", "Discard", "negative"):
            e.sender.value = self.system
            return
        self.system = e.value
        self.tests.file = self.tests.index = self.tests.hash = None
        self.pages.rel = self.pages.hash = None
        self.tests.dirty = self.pages.dirty = False
        self.tests.warnings = []
        self.tests.load_refs()
        for editor in (self.tests, self.pages):
            editor.list_view.refresh()
            editor.editor.refresh()
