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
from webapp.ui.components import field as labelled, icon_button, split_test_id, styled_table, text
from webapp.ui.theme import icon, svg

VALUE_KINDS = {"text": "Text", "auto": "Auto", "number": "Number", "json": "JSON"}
KIND_LABELS = {"screen": "Page", "commonAction": "Common action", "testCase": "Reusable test",
               "API_REQUEST": "API request"}
KIND_TONES = {"screen": "kind-page", "commonAction": "kind-common", "testCase": "kind-reuse", "API_REQUEST": "kind-api"}
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
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog"):
        ui.label(message).classes("any-dialog-text")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38")
            ui.button(ok, color=None, on_click=lambda: dialog.submit(True)) \
                .classes("any-danger" if color == "negative" else "any-primary")
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

        with ui.element("div").classes("any-split"):
            with ui.element("section").classes("any-card any-split-list").props('aria-label="Test cases"'):
                with ui.element("div").classes("any-list-head"):
                    self.search = ui.input(placeholder="Search tests").props(
                        'clearable aria-label="Search tests"').classes("flex-1")
                    icon_button("plus", "New test case", self.new_test_dialog)
                    icon_button("rotate-cw", "Reload", self.reload_list)
                self.list_view()
            with ui.element("section").classes("any-card any-split-main").props('aria-label="Test case editor"'):
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
            ui.label("Pick a system.").classes("any-empty any-muted")
            return
        refs, errors = self.svc.tests.list_tests(system)
        for err in errors:
            ui.label(f"Could not read {err}").classes("any-err-line px-6")
        rows = []
        for r in refs:
            name, code = split_test_id(r.test_id)
            rows.append({"key": f"{r.file}#{r.index}", "file": r.file, "index": r.index, "test_id": r.test_id,
                         "name": name, "code": code, "tag_list": r.tags, "tags": ", ".join(r.tags),
                         "steps": r.steps, "dup": r.duplicate})
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="key", pagination=15, columns=[
                {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
                {"name": "file", "label": "File", "field": "file", "align": "left", "sortable": True},
                {"name": "steps", "label": "Steps", "field": "steps", "align": "right"},
            ]).classes("w-full is-clickable is-compact")
            styled_table(table, len(rows))
            table.add_slot("body-cell-test_id", f'''
                <q-td :props="props">
                  <div class="any-tc-name" :title="props.row.test_id">{{{{ props.row.name }}}}
                    <span v-if="props.row.dup" class="any-dup" title="Duplicate test ID"
                          aria-label="Duplicate test ID">{svg("triangle-alert")}</span></div>
                  <div v-if="props.row.code" class="any-tc-code">{{{{ props.row.code }}}}</div>
                  <div class="any-tags mt-1">
                    <span v-for="t in props.row.tag_list" :key="t" class="any-tag">{{{{ t }}}}</span>
                  </div>
                </q-td>''')
            table.add_slot("body-cell-file", '<q-td :props="props" class="any-num any-small">{{ props.value }}</q-td>')
            table.add_slot("body-cell-steps", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.add_slot("no-data", '<div class="any-muted text-sm">'
                                      'No test cases for this system yet - use + to create one.</div>')
            self.search.bind_value(table, "filter")
            table.on("rowClick", lambda e: self.open(e.args[1]["file"], e.args[1]["index"]))

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
            ui.label("Select a test case on the left, or create a new one with +.").classes("any-empty any-muted")
            return
        with ui.element("div").classes("any-editor-head"):
            with ui.element("div").classes("any-editor-title"):
                text("h2", self.meta.get("testCaseID") or "(no id)", "any-title")
                text("span", f"{self.file} #{self.index}", "any-tc-code")
                self.dirty_badge = text("span", "unsaved", "any-pill pill-warn")
                self.dirty_badge.set_visibility(self.dirty)
            with ui.element("div").classes("any-toolbar"):
                ui.toggle({False: "Form", True: "JSON"}, value=self.json_mode, on_change=self.toggle_json) \
                    .props("no-caps unelevated").classes("any-segmented")
                ui.button("Check", icon=icon("list-checks"), color=None, on_click=lambda: self.validate(True)) \
                    .classes("any-btn is-38")
                ui.button("Revert", icon=icon("undo-2"), color=None, on_click=self.revert).classes("any-btn is-38")
                ui.button("Copy", icon=icon("copy"), color=None, on_click=self.copy_dialog) \
                    .classes("any-btn is-38").tooltip("Save as a new test case")
                icon_button("trash-2", "Delete test case", self.delete).classes("is-danger")
                ui.button("Run", icon=icon("play-solid"), color=None, on_click=self.run_this) \
                    .classes("any-btn is-38").tooltip("Run only this test case")
                ui.button("Save", icon=icon("save"), color=None, on_click=self.save).classes("any-primary")
        self.validation_view()
        if self.json_mode:
            self.json_editor()
        else:
            self.meta_form()
            self.steps_view()

    @ui.refreshable_method
    def validation_view(self) -> None:
        if self.warnings:
            with ui.expansion(f"{len(self.warnings)} warning(s)", icon=icon("triangle-alert")) \
                    .classes("any-disclosure is-warn"):
                with ui.element("ul").classes("any-warn-list"):
                    for w in self.warnings:
                        text("li", w)

    def meta_form(self) -> None:
        def bind(key):
            def handler(e):
                self.meta[key] = e.value
                self.touch()
            return handler

        with ui.element("div").classes("any-form"):
            with ui.element("div").classes("any-form-row"):
                with labelled("Test case ID", "tc-id").classes("is-grow"):
                    ui.input(value=self.meta.get("testCaseID") or "", on_change=bind("testCaseID")) \
                        .props('for="tc-id"').classes("w-full mono-input")
                with labelled("System", "tc-system"):
                    ui.select(self.gen.all_systems(), value=self.meta.get("system"), with_input=True,
                              new_value_mode="add-unique", on_change=bind("system")) \
                        .props('for="tc-system"').classes("w-40")
                with labelled("Env", "tc-env"):
                    ui.input(value=self.meta.get("env") or "", on_change=bind("env")) \
                        .props('for="tc-env"').classes("w-28")
                with labelled("Module", "tc-module"):
                    ui.input(value=self.meta.get("module") or "", on_change=bind("module")) \
                        .props('for="tc-module"').classes("w-40")
            with labelled("Tags", "tc-tags"):
                ui.input_chips(value=list(self.meta.get("tags") or []), new_value_mode="add-unique",
                               on_change=bind("tags")).props('for="tc-tags"').classes("w-full any-multi")
            with labelled("Description", "tc-desc"):
                ui.textarea(value=self.meta.get("description") or "", on_change=bind("description")) \
                    .props('autogrow for="tc-desc"').classes("w-full")

    @ui.refreshable_method
    def steps_view(self) -> None:
        with ui.element("div").classes("any-steps"):
            with ui.element("div").classes("any-section-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h3", "Steps", "any-title")
                    text("span", str(len(self.steps)), "any-count-pill mono")
                with ui.element("div").classes("any-head-right"):
                    for kind, label in (("screen", "Page"), ("commonAction", "Common action"),
                                        ("testCase", "Reusable test")):
                        ui.button(label, icon=icon("plus"), color=None, on_click=lambda k=kind: self.add_step(k)) \
                            .classes("any-btn is-32")
            if not self.steps:
                ui.label("No steps yet.").classes("any-muted text-sm")
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

        with ui.element("div").classes("any-step"):
            with ui.element("div").classes("any-step-head"):
                text("span", str(i + 1), "any-step-no")
                text("span", KIND_LABELS.get(step.kind, step.kind), f"any-kind {KIND_TONES.get(step.kind, '')}")
                ui.select(options, value=step.target or None, with_input=True, new_value_mode="add-unique",
                          on_change=set_target).props(f'aria-label="Step {i + 1} target"').classes("flex-1 min-w-0")
                with ui.element("div").classes("any-step-tools"):
                    icon_button("arrow-up", "Move step up", lambda: self.move(i, -1), "is-32").set_enabled(i > 0)
                    icon_button("arrow-down", "Move step down", lambda: self.move(i, 1), "is-32") \
                        .set_enabled(i < len(self.steps) - 1)
                    icon_button("copy", "Duplicate step", lambda: self.duplicate(i), "is-32")
                    icon_button("x", "Remove step", lambda: self.remove(i), "is-32").classes("is-danger")
            if step.kind == "commonAction":
                def set_override(e):
                    if e.value:
                        step.extra["override"] = e.value
                    else:
                        step.extra.pop("override", None)
                    self.touch()
                with labelled("Override (screen|field|value)", f"step-{i}-override"):
                    ui.input(value=step.extra.get("override", ""), on_change=set_override) \
                        .props(f'for="step-{i}-override"').classes("w-full")
            others = {k: v for k, v in step.extra.items() if k != "override"}
            if others:
                ui.label("Other step keys (edit in JSON view): " + ", ".join(others)).classes("any-muted text-xs")
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

            with ui.element("div").classes("any-form-line"):
                ui.select(key_options, value=row.key or None, with_input=True, new_value_mode="add-unique",
                          on_change=set_key).props('aria-label="Field"').classes("w-64 max-w-full")
                ui.select(VALUE_KINDS, value=row.kind, on_change=set_kind) \
                    .props('aria-label="Value type"').classes("w-28")
                if row.kind == "auto":
                    ui.label("auto-generated (null)").classes("flex-1 any-muted text-sm italic")
                else:
                    placeholder = {"json": '{"type": "confirmtext"}', "number": "0"}.get(row.kind, "value or click")
                    ui.input(value=row.raw, on_change=set_raw, placeholder=placeholder) \
                        .props('aria-label="Value"').classes("flex-1 min-w-40")
                icon_button("x", "Remove field", lambda r=row: self.remove_row(step, r), "is-32")
        with ui.element("div").classes("any-form-line is-actions"):
            ui.button("Field", icon=icon("plus"), color=None, on_click=lambda: self.add_row(step)) \
                .props("flat").classes("any-ghost")
            if fields:
                ui.button(f"All {len(fields)} page fields", icon=icon("plus"), color=None,
                          on_click=lambda: self.add_all_rows(step, fields)).props("flat").classes("any-ghost")

    def json_editor(self) -> None:
        try:
            source = json.dumps(self.build(), indent=2, ensure_ascii=False)
        except ValueError as e:
            source = f"// Fix form errors first: {e}"
        self.code = ui.codemirror(source, language="JSON", line_wrapping=True).classes("any-code h-[60vh]")
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
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[28rem]"):
            text("h2", title, "any-title")
            with labelled("Test case ID", "dlg-tc-id"):
                test_id = ui.input(value=default_id).props('for="dlg-tc-id"').classes("w-full")
            with labelled("File", "dlg-file"):
                file_sel = ui.select(files + [new_label], value=self.file if self.file in files
                                     else (files[0] if files else new_label)).props('for="dlg-file"').classes("w-full")
            with labelled("New file name", "dlg-new-file") as new_box:
                new_name = ui.input(placeholder="MyTests.json").props('for="dlg-new-file"').classes("w-full")
            new_box.bind_visibility_from(file_sel, "value", value=new_label)

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

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                ui.button("Create", color=None, on_click=ok).classes("any-primary")
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

        with ui.element("div").classes("any-split"):
            with ui.element("section").classes("any-card any-split-list").props('aria-label="Pages"'):
                with ui.element("div").classes("any-list-head"):
                    self.search = ui.input(placeholder="Search pages").props(
                        'clearable aria-label="Search pages"').classes("flex-1")
                    icon_button("plus", "New page", self.new_page_dialog)
                    icon_button("rotate-cw", "Reload", lambda: self.list_view.refresh())
                self.list_view()
            with ui.element("section").classes("any-card any-split-main").props('aria-label="Page editor"'):
                self.editor()

    @ui.refreshable_method
    def list_view(self) -> None:
        system = self.gen.system
        if not system:
            ui.label("Pick a system.").classes("any-empty any-muted")
            return
        refs = self.svc.pom.list_pages(system)
        rows = [{"rel": r.rel, "name": r.name, "folder": r.rel.rpartition("/")[0], "fields": r.fields,
                 "dup": r.duplicate} for r in refs]
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="rel", pagination=15, columns=[
                {"name": "name", "label": "Page", "field": "name", "align": "left", "sortable": True},
                {"name": "folder", "label": "Folder", "field": "folder", "align": "left", "sortable": True},
                {"name": "fields", "label": "Fields", "field": "fields", "align": "right", "sortable": True},
            ]).classes("w-full is-clickable is-compact")
            styled_table(table, len(rows))
            table.add_slot("body-cell-name", f'''
                <q-td :props="props"><span class="any-tc-name">{{{{ props.value }}}}</span>
                  <span v-if="props.row.dup" class="any-dup"
                        title="Another page has this name - the last one loaded wins"
                        aria-label="Duplicate page name">{svg("triangle-alert")}</span>
                </q-td>''')
            table.add_slot("body-cell-folder", f'''
                <q-td :props="props">
                  <span v-if="props.value" class="any-file">{svg("folder")}{{{{ props.value }}}}</span>
                  <span v-else class="any-muted">—</span>
                </q-td>''')
            table.add_slot("body-cell-fields", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.add_slot("no-data", '<div class="any-muted text-sm">'
                                      'No pages for this system yet - use + to create one.</div>')
            self.search.bind_value(table, "filter")
            table.on("rowClick", lambda e: self.open(e.args[1]["rel"]))

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
            ui.label("Select a page on the left, or create a new one with +.").classes("any-empty any-muted")
            return
        name = self.rel.rpartition("/")[2][:-5]
        with ui.element("div").classes("any-editor-head"):
            with ui.element("div").classes("any-editor-title"):
                text("h2", name, "any-title")
                text("span", f"Pages/{self.gen.system}/{self.rel}", "any-tc-code")
                self.dirty_badge = text("span", "unsaved", "any-pill pill-warn")
                self.dirty_badge.set_visibility(self.dirty)
            with ui.element("div").classes("any-toolbar"):
                ui.toggle({False: "Form", True: "JSON"}, value=self.json_mode, on_change=self.toggle_json) \
                    .props("no-caps unelevated").classes("any-segmented")
                ui.button("Revert", icon=icon("undo-2"), color=None, on_click=self.revert).classes("any-btn is-38")
                ui.button("Save", icon=icon("save"), color=None, on_click=self.save).classes("any-primary")
        if self.json_mode:
            try:
                source = json.dumps(self.build(), indent=2, ensure_ascii=False)
            except ValueError as e:
                source = f"// Fix form errors first: {e}"
            self.code = ui.codemirror(source, language="JSON", line_wrapping=True).classes("any-code h-[65vh]")
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

        def page_input(label: str, key: str) -> None:
            with labelled(label, f"pg-{key}").classes("is-grow"):
                ui.input(value=self.page.get(key) or "", on_change=bind(key)) \
                    .props(f'for="pg-{key}"').classes("w-full mono-input")

        with ui.expansion("Page settings (URL, title and error checks)", icon=icon("settings")) \
                .classes("any-disclosure is-section"):
            with ui.element("div").classes("any-disclosure-body"):
                with ui.element("div").classes("any-form-row"):
                    page_input("path (appended to base_url)", "path")
                    page_input("fullpath (absolute URL)", "fullpath")
                with ui.element("div").classes("any-form-row"):
                    page_input("titleLocator", "titleLocator")
                    page_input("titleContains", "titleContains")
                with ui.element("div").classes("any-form-row"):
                    for key in ("errorLocators", "errorContains"):
                        value = self.page.get(key)
                        with labelled(key, f"pg-{key}").classes("is-grow"):
                            ui.input_chips(value=list(value) if isinstance(value, list) else [],
                                           new_value_mode="add-unique", on_change=bind(key)) \
                                .props(f'for="pg-{key}"').classes("w-full any-multi")
                others = [k for k in self.page if k not in PAGE_KEYS]
                if others:
                    ui.label("Other page keys (edit in JSON view): " + ", ".join(others)) \
                        .classes("any-muted text-xs")

    @ui.refreshable_method
    def fields_view(self) -> None:
        autogen = self.svc.pom.autogen_rules()
        with ui.element("div").classes("any-steps"):
            with ui.element("div").classes("any-section-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h3", "Fields", "any-title")
                    text("span", str(len(self.rows)), "any-count-pill mono")
                    ui.input(placeholder="Filter fields", value=self.field_filter, on_change=self._set_filter) \
                        .props('clearable debounce=300 aria-label="Filter fields"').classes("w-56")
                ui.button("Field", icon=icon("plus"), color=None, on_click=self.add_field).classes("any-btn is-32")
            with ui.element("div").classes("any-grid-scroll"):
                with ui.element("div").classes("any-field-grid is-head").props('aria-hidden="true"'):
                    for label in ("Name", "Type", "Locator", "Index", "autoGen", ""):
                        ui.label(label)
                needle = (self.field_filter or "").casefold()
                shown = [r for r in self.rows if not needle or needle in r.name.casefold()
                         or needle in str(r.cfg.get("locator", "")).casefold()]
                for row in shown:
                    self.field_row(row, autogen)
            if needle and len(shown) < len(self.rows):
                ui.label(f"{len(self.rows) - len(shown)} field(s) hidden by filter").classes("any-muted text-xs")

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
        with ui.element("div").classes("any-field-grid"):
            ui.input(value=row.name, on_change=set_name).props('aria-label="Name"').classes("mono-input")
            ui.select(types, value=row.cfg.get("type"), with_input=True, new_value_mode="add-unique",
                      on_change=set_cfg("type")).props('aria-label="Type"')
            ui.input(value=str(row.cfg.get("locator", "")), on_change=set_cfg("locator")) \
                .props('aria-label="Locator"').classes("mono-input")
            ui.number(value=row.cfg.get("index"), precision=0, on_change=set_cfg("index", int)) \
                .props('aria-label="Index"').classes("mono-input")
            ui.select(gens, value=row.cfg.get(gen_key), with_input=True, clearable=True,
                      on_change=set_cfg(gen_key)).props('aria-label="autoGen"')
            with ui.element("div").classes("flex items-center no-wrap"):
                more = icon_button("braces", "All keys: " + (", ".join(extra) if extra else "none"),
                                   lambda: self.field_dialog(row), "is-32")
                if extra:
                    more.classes("is-active")
                icon_button("x", "Remove field", lambda: self.remove_field(row), "is-32").classes("is-danger")

    def field_dialog(self, row: FieldRow) -> None:
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[36rem]"):
            text("h2", f"Field '{row.name}' - all keys", "any-title")
            ui.label("role, name, nested, tableId, waitForNewTab, focus, placeholder, label, text, contains, ...") \
                .classes("any-muted text-xs")
            code = ui.codemirror(json.dumps(row.cfg, indent=2, ensure_ascii=False), language="JSON") \
                .classes("any-code h-72")

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

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                ui.button("Apply", color=None, on_click=apply).classes("any-primary")
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
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[28rem]"):
            text("h2", f"New page in {system}", "any-title")
            with labelled("Folder (optional - type a new one)", "dlg-folder"):
                folder = ui.select(folders, with_input=True, new_value_mode="add-unique", clearable=True) \
                    .props('for="dlg-folder"').classes("w-full")
            with labelled("Page name", "dlg-page"):
                name = ui.input(placeholder="loginPage").props('for="dlg-page"').classes("w-full")

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

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                ui.button("Create", color=None, on_click=ok).classes("any-primary")
        dialog.open()


# ───────────── tab ─────────────

class GeneratorPanel:
    def __init__(self, on_run: Callable[[str], None] | None = None):
        self.svc = services()
        self.on_run = on_run  # shell callback: follow a started run on the Control tab
        systems = self.all_systems()
        default = self.svc.env.load().get("systemConfig")
        self.system: str | None = default if default in systems else (systems[0] if systems else None)
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("div").classes("any-page-head"):
                with ui.element("div").classes("any-head-left"):
                    with labelled("System", "gen-system"):
                        ui.select(systems, value=self.system, on_change=self.on_system) \
                            .props('for="gen-system"').classes("w-48")
                    ui.label("Edits are saved straight to the workspace JSON files.").classes("any-muted text-sm")
                with ui.tabs().props('no-caps inline-label indicator-color="transparent" '
                                     'aria-label="Generator sections"').classes("any-subtabs") as tabs:
                    tests_tab = ui.tab("Test cases", icon=icon("list-checks"))
                    pages_tab = ui.tab("Pages", icon=icon("app-window"))
                self.sections, self.pages_tab = tabs, pages_tab
            with ui.tab_panels(tabs, value=tests_tab).classes("w-full bg-transparent"):
                with ui.tab_panel(tests_tab).classes("p-0"):
                    self.tests = TestEditor(self)
                with ui.tab_panel(pages_tab).classes("p-0"):
                    self.pages = PageEditor(self)
        self.tests.load_refs()

    async def show_default(self) -> None:
        """Default view when the Generator tab is opened: Pages, with the first page open in Form view.
        A page that is already open (possibly with unsaved edits) stays open."""
        self.sections.set_value(self.pages_tab)
        if self.pages.rel is not None or not self.system:
            return
        refs = sorted(self.svc.pom.list_pages(self.system), key=lambda r: r.rel.casefold())
        if refs:
            self.pages.json_mode = False
            await self.pages.open(refs[0].rel)

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
