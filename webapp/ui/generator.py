"""Generator tab: edit test cases and page objects (POM JSON) for a system."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Callable

from nicegui import ui

from webapp.services.env_config import FIELDS as ENV_FIELDS
from webapp.services.json_files import ConflictError, PathError
from webapp.services.refactor import Plan, Refactor, RefactorError, apply_plan
from webapp.services.tidy import analyze as tidy_analyze, build_plan as tidy_plan
from webapp.services.usage import Usage, UsageIndex
from webapp.services.pom_service import FIELD_TYPES, PAGE_KEYS
from webapp.services.run_manager import RunConfig
from webapp.services.test_case_service import STEP_KINDS
from webapp.auth import audit, guard
from webapp.state import services
from webapp.ui.components import field as labelled, icon_button, split_test_id, styled_table, text
from webapp.ui.ai_panels import LOCATOR_KEYS, open_draft_dialog
from webapp.ui.locator_dialog import open_locator_check
from webapp.ui.page_scan_dialog import open_page_scan
from webapp.ui.recorder_dialog import open_recorder
from webapp.ui.step_actions import (
    HINTS, KEY_NAMES, action_options, apply_action, default_action, has_value_slot, json_value, loop_of,
    ref_value, row_action, set_json_value, split_key, with_loop,
)
from webapp.ui.theme import icon, svg

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
                    self.draft_button = icon_button("sparkles", "Draft a test with AI",
                                                    lambda: guard("ai.write", "draft") and open_draft_dialog(self))
                    self.draft_button.props('data-testid="ai-draft-open"')
                    self.draft_button.set_visibility(self.svc.ai.enabled())
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
                    if step.kind == "screen" and step.target in self._screens:
                        icon_button("external-link", "Open this page in the Pages tab",
                                    lambda t=step.target: self.gen.open_page(t), "is-32")
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
            others = {k: v for k, v in step.extra.items() if k not in ("override", "index")}
            if others:
                ui.label("Other step keys (edit in JSON view): " + ", ".join(others)).classes("any-muted text-xs")
            if step.kind == "screen":
                self.index_input(i, step)
                self.form_rows(step)

    def index_input(self, i: int, step: StepModel) -> None:
        """For pages with repeated sections: the occurrence the fields marked "indexed" act on."""
        system = self.meta.get("system") or self.gen.system
        configs = self.svc.pom.field_configs(system, step.target) if step.target and system else {}
        if not (any(c.get("indexed") for c in configs.values()) or "index" in step.extra):
            return

        def set_index(e):
            if e.value in (None, ""):
                step.extra.pop("index", None)
            else:
                step.extra["index"] = int(e.value)
            self.touch()

        with ui.element("div").classes("any-form-row"):
            with labelled("Occurrence (index, from 0)", f"step-{i}-index"):
                ui.number(value=step.extra.get("index"), min=0, precision=0, on_change=set_index) \
                    .props(f'for="step-{i}-index" placeholder="0"').classes("w-40")
            ui.label("Fields marked indexed on the page use this occurrence: 1 acts on the second row, "
                     "second section, ...").classes("any-muted text-xs self-end pb-2")

    def form_rows(self, step: StepModel) -> None:
        system = self.meta.get("system") or self.gen.system
        configs = self.svc.pom.field_configs(system, step.target) if step.target and system else {}
        fields = list(configs)
        custom = self.svc.pom.custom_fields()
        for row in step.rows:
            name, _ = split_key(row.key)
            cfg = configs.get(name)
            key_options = sorted(set(fields) | ({name} if name else set()), key=str.casefold) + \
                [c for c in custom if c != name]

            def set_name(e, r=row):
                old = split_key(r.key)[1]
                r.key = (e.value or "") + old
                new_cfg = configs.get(e.value or "")
                if new_cfg and r.kind == "text" and not r.raw.strip():
                    apply_action(r, default_action(new_cfg), new_cfg)  # a button starts as Click, a text box as Fill
                self.touch()
                self.steps_view.refresh()

            def set_action(e, r=row, c=cfg):
                apply_action(r, e.value, c)
                self.touch()
                self.steps_view.refresh()

            def set_raw(e, r=row):
                r.raw = e.value or ""
                self.touch()

            action = row_action(row, cfg)
            with ui.element("div").classes("any-form-line"):
                ui.select(key_options, value=name or None, with_input=True, new_value_mode="add-unique",
                          on_change=set_name).props('aria-label="Field"').classes("w-64 max-w-full")
                ui.select(action_options(cfg), value=action, on_change=set_action) \
                    .props('aria-label="Action"').classes("w-48")
                self.value_control(row, action, cfg, set_raw)
                self.repeat_control(row)
                icon_button("x", "Remove field", lambda r=row: self.remove_row(step, r), "is-32")
        with ui.element("div").classes("any-form-line is-actions"):
            ui.button("Field", icon=icon("plus"), color=None, on_click=lambda: self.add_row(step)) \
                .props("flat").classes("any-ghost")
            if fields:
                ui.button(f"All {len(fields)} page fields", icon=icon("plus"), color=None,
                          on_click=lambda: self.add_all_rows(step, fields, configs)).props("flat").classes("any-ghost")

    def value_control(self, row: FormRow, action: str, cfg: dict | None, set_raw) -> None:
        """The part of a row that depends on the action: a value, a key, seconds, JSON, or just a hint."""
        if action in ("fill", "select", "choose"):
            hint = {"choose": "option to choose, e.g. No", "select": "option to select"}.get(action, "value")
            ui.input(value=row.raw, on_change=set_raw, placeholder=hint) \
                .props('aria-label="Value"').classes("flex-1 min-w-40")
            self.insert_menu(row)
        elif action == "key":
            ui.select(list(KEY_NAMES), value=json_value(row) or None, with_input=True, new_value_mode="add-unique",
                      on_change=lambda e, r=row: (set_json_value(r, e.value or ""), self.touch())) \
                .props('aria-label="Key"').classes("flex-1 min-w-40")
        elif action == "wait":
            ui.number(value=float(json_value(row) or 0), min=0, step=1,
                      on_change=lambda e, r=row: (set_json_value(r, str(int(e.value or 0))), self.touch())) \
                .props('aria-label="Seconds"').classes("flex-1 min-w-40")
        elif action == "custom":
            ui.input(value=row.raw, on_change=set_raw, placeholder='{"type": "..."}') \
                .props('aria-label="Custom action JSON"').classes("flex-1 min-w-40 mono-input")
        else:
            ui.label(HINTS.get(action, "")).classes("flex-1 any-muted text-sm italic")

    def insert_menu(self, row: FormRow) -> None:
        def pick(kind: str) -> None:
            self.ref_dialog(kind, lambda value: (setattr(row, "raw", value), self.touch(), self.steps_view.refresh()))

        button = icon_button("braces", "Insert a value from .env or from an earlier step", None, "is-32")
        with button:
            with ui.menu():
                ui.menu_item("From a .env variable...", on_click=lambda: pick("ENV"))
                ui.menu_item("From a value saved earlier in the test...", on_click=lambda: pick("DICT"))

    def ref_dialog(self, kind: str, on_pick: Callable[[str], None]) -> None:
        system = self.meta.get("system") or self.gen.system
        names: list[str] = []
        if kind == "ENV":  # names only: values stay in .env
            names = sorted(set(self.svc.env.load_other()) | {
                c["key"] for c in (self.svc.credential_status(system, self.meta.get("env") or "dev") or [])},
                key=str.casefold)
        what = "the .env variable" if kind == "ENV" else "the saved value (set earlier in the test)"
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog"):
            text("h2", "Insert a value", "any-title")
            ui.label(f"Name {what}; the engine reads it at run time ({kind}.NAME).").classes("any-muted text-xs")
            pick = ui.select(names, with_input=True, new_value_mode="add-unique") \
                .props('aria-label="Name"').classes("w-full") if names else \
                ui.input(placeholder="name").props('aria-label="Name"').classes("w-full")

            def ok() -> None:
                value = ref_value(kind, str(pick.value or ""))
                if value:
                    on_pick(value)
                dialog.close()

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                ui.button("Insert", color=None, on_click=ok).classes("any-primary")
        dialog.open()

    def repeat_control(self, row: FormRow) -> None:
        kind, count = loop_of(row.key)
        if kind == "LLoop":  # one pass per match of the locator: shown, edited in JSON view
            ui.label(f"each match x{count}").classes("any-muted text-xs")
            return

        def set_count(e, r=row):
            value = str(e.value or "").strip()
            if value or loop_of(r.key)[0]:
                r.key = with_loop(r.key, "ELoop" if value else "", value)
            self.touch()

        ui.input(value=count, on_change=set_count, placeholder="x1") \
            .props('aria-label="Repeat" dense').classes("w-16").tooltip("Repeat the action N times (ELoop)")

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

    def add_all_rows(self, step: StepModel, fields: list[str], configs: dict[str, dict]) -> None:
        present = {split_key(r.key)[0] for r in step.rows}
        for name in fields:
            if name in present:
                continue
            row = FormRow(name, "auto", "")
            if default_action(configs.get(name)) == "click":
                apply_action(row, "click")  # clicks say what they do; text fields stay generated
            elif has_value_slot(configs.get(name)):
                row.kind = "text"  # needs an answer from the author
            step.rows.append(row)
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
        if tc is None or not guard("edit", "save test case", self.file or ""):
            return
        try:
            self.index, self.hash = self.svc.tests.save(self.gen.system, self.file, self.index, tc, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(f"Not saved: {e}", type="negative", multi_line=True, timeout=8000)
            return
        audit("testcase.save", f"{self.gen.system}/{self.file}", test_id=tc.get("testCaseID"))
        self._set_model(tc)
        self.validate(notify=False)
        self.list_view.refresh()
        ui.notify(f"Saved {tc.get('testCaseID')} to {self.file}", type="positive")

    async def run_this(self) -> None:
        """Run just this test case with the saved defaults (.env) for browser options."""
        if not guard("run", "run test case"):
            return
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
        audit("run.start", run.id, test_id=test_id)
        ui.notify(f"Running {test_id} ({run.id})", type="positive")
        if self.gen.on_run:
            self.gen.on_run(run.id)

    async def revert(self) -> None:
        if self.file is None:
            return
        self.dirty = False
        await self.open(self.file, self.index)

    async def delete(self) -> None:
        if self.file is None or not guard("edit", "delete test case"):
            return
        if not await confirm(
                f"Delete test case '{self.meta.get('testCaseID')}' from {self.file}?", "Delete", "negative"):
            return
        try:
            self.svc.tests.delete(self.gen.system, self.file, self.index, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(str(e), type="negative")
            return
        audit("testcase.delete", f"{self.gen.system}/{self.file}", test_id=self.meta.get("testCaseID"))
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
        if not guard("edit", "create test case"):
            return
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
        audit("testcase.create", f"{self.gen.system}/{rel}", test_id=tc["testCaseID"])
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
        self.usage: Usage | None = None  # who uses this page's fields (tests, common actions)
        self.orig_names: list[str] = []

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
        try:
            usage = UsageIndex(self.svc.tests).scan(system)
        except (OSError, ValueError):
            usage = Usage()
        rows = [{"rel": r.rel, "name": r.name, "folder": r.rel.rpartition("/")[0], "fields": r.fields,
                 "used": usage.page_count(r.name), "dup": r.duplicate} for r in refs]
        has_folders = any(r["folder"] for r in rows)
        columns = [{"name": "name", "label": "Page", "field": "name", "align": "left", "sortable": True}]
        if has_folders:  # the column is only worth its width when some page is in a folder
            columns.append({"name": "folder", "label": "Folder", "field": "folder", "align": "left",
                            "sortable": True})
        columns += [{"name": "fields", "label": "Fields", "field": "fields", "align": "right", "sortable": True},
                    {"name": "used", "label": "Used by", "field": "used", "align": "right", "sortable": True}]
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="rel", pagination=15, columns=columns) \
                .classes("w-full is-clickable is-compact")
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
            table.add_slot("body-cell-used", '''
                <q-td :props="props" class="any-num">
                  <span v-if="props.value">{{ props.value }}</span>
                  <span v-else class="any-muted" title="No test or common action uses this page">unused</span>
                </q-td>''')
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
        self.orig_names = list((data.get("fields") or {}).keys())
        self.refresh_usage()
        self.editor.refresh()

    def refresh_usage(self) -> None:
        try:
            self.usage = UsageIndex(self.svc.tests).scan(self.gen.system) if self.gen.system else None
        except (OSError, ValueError):
            self.usage = None

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
                with ui.button("Maintain", icon=icon("list-checks"), color=None).classes("any-btn is-38") \
                        .props('data-testid="page-maintain"').tooltip("Rename and tidy this page"):
                    with ui.menu():
                        ui.menu_item("Rename page...", on_click=self.rename_page_dialog)
                        ui.menu_item("Rename a field...", on_click=self.rename_field_dialog)
                        ui.menu_item("Tidy up this page...", on_click=self.tidy_dialog)
                ui.button("Revert", icon=icon("undo-2"), color=None, on_click=self.revert).classes("any-btn is-38")
                ui.button("Save", icon=icon("save"), color=None, on_click=self.save).classes("any-primary")
        self.usage_view()
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
                with ui.element("div").classes("flex gap-2"):
                    ui.button("From page", icon=icon("scan-search"), color=None,
                              on_click=lambda: open_page_scan(self)).props('data-testid="scan-open"') \
                        .classes("any-btn is-32").tooltip("Add fields by scanning the live page")
                    ui.button("Field", icon=icon("plus"), color=None, on_click=self.add_field) \
                        .classes("any-btn is-32")
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
            uses = self.usage.field_uses(self._page_name(), row.name) if self.usage and row.name else None
            name_box = ui.input(value=row.name, on_change=set_name).props('aria-label="Name"').classes("mono-input")
            if uses is not None:
                name_box.classes("is-unused" if not uses else "")
                name_box.tooltip("Not used by any test or common action" if not uses else
                                 f"Used in {len(uses)} step{'' if len(uses) == 1 else 's'}")
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

                def check() -> None:
                    if guard("check", "check locator"):
                        open_locator_check(self.gen.system, self._page_name(), dict(self.page), row.name,
                                           dict(row.cfg), on_apply=lambda c: self.set_locator(row, c))

                icon_button("scan-search", "Check locator on the live page", check, "is-32")
                icon_button("x", "Remove field", lambda: self.remove_field(row), "is-32").classes("is-danger")

    def set_locator(self, row: FieldRow, locator_keys: dict) -> None:
        """Replace the row's locator keys (an accepted suggestion); the page stays unsaved until Save."""
        keep = {k: v for k, v in row.cfg.items() if k not in LOCATOR_KEYS}
        row.cfg = {**keep, **locator_keys}
        self.touch()
        self.editor.refresh()

    def _page_name(self) -> str:
        return (self.rel or "").rpartition("/")[2].removesuffix(".json") or "New page"

    @ui.refreshable_method
    def usage_view(self) -> None:
        if self.rel is None or self.usage is None:
            return
        owners: dict[tuple[str, str], object] = {}
        for use in self.usage.page_uses(self._page_name()):
            owners.setdefault((use.file, use.owner), use)
        if not owners:
            with ui.element("div").classes("any-form-row"):
                text("span", "Not used by any test or common action", "any-pill pill-warn")
            return
        n = len(owners)
        with ui.expansion(f"Used by {n} test{'' if n == 1 else 's'} / common action{'' if n == 1 else 's'}",
                          icon=icon("book-open")).classes("any-disclosure").props('data-testid="page-used-by"'):
            with ui.element("div").classes("any-disclosure-body"):
                for (file, owner), use in sorted(owners.items(), key=lambda kv: (kv[0][0].casefold(), kv[0][1])):
                    if use.source == "test" and use.folder == self.gen.system:
                        ui.button(f"{owner}  -  {file}", color=None,
                                  on_click=lambda u=use: self.gen.open_test(u.rel, u.index)) \
                            .props("flat no-caps align=left").classes("any-ghost")
                    else:
                        text("div", f"{owner}  -  {file}", "any-muted text-sm")

    # ───── maintenance: rename, tidy (all-or-nothing, previewed) ─────

    def _ready_for_refactor(self) -> bool:
        if not guard("edit", "change pages and tests"):
            return False
        if self.dirty or self.gen.tests.dirty:
            ui.notify("Save or revert your unsaved edits first: this changes several files.", type="warning")
            return False
        return True

    def refactor_dialog(self, title: str, intro: str, build_inputs: Callable[[], None],
                        make_plan: Callable[[], Plan], audit_action: str, audit_target: str,
                        reopen: Callable[[], str | None] = lambda: None, apply_label: str = "Apply") -> None:
        """Inputs, then Preview (what would change, nothing written), then Apply (all files or none)."""
        state: dict = {"plan": None}
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[40rem]"):
            text("h2", title, "any-title")
            ui.label(intro).classes("any-muted text-sm")
            build_inputs()
            result = ui.element("div").classes("flex flex-col gap-1 w-full")

            def preview() -> None:
                result.clear()
                state["plan"] = None
                try:
                    plan = make_plan()
                except (RefactorError, OSError, ValueError) as e:
                    with result:
                        ui.label(str(e)).classes("any-err-line")
                    apply_button.set_enabled(False)
                    return
                with result:
                    if plan.empty:
                        ui.label("Nothing to change.").classes("any-muted text-sm")
                    else:
                        text("div", "These files will change:", "any-eyebrow")
                        for line in plan.summary():
                            text("div", line, "any-small")
                state["plan"] = None if plan.empty else plan
                apply_button.set_enabled(not plan.empty)

            async def apply() -> None:
                plan = state["plan"]
                if plan is None or not guard("edit", audit_action, audit_target):
                    return
                try:
                    apply_plan(plan)
                except (ConflictError, OSError, ValueError) as e:
                    ui.notify(f"Nothing was changed: {e}", type="negative", multi_line=True, timeout=8000)
                    return
                audit(audit_action, audit_target, files=len(plan.docs) + len(plan.renames))
                dialog.close()
                ui.notify(f"Done: {len(plan.docs)} file(s) updated"
                          + (f", {len(plan.renames)} renamed" if plan.renames else ""), type="positive")
                await self.after_refactor(reopen())

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                ui.button("Preview", icon=icon("search"), color=None, on_click=preview) \
                    .props('data-testid="refactor-preview"').classes("any-btn is-38")
                apply_button = ui.button(apply_label, color=None, on_click=apply) \
                    .props('data-testid="refactor-apply"').classes("any-primary")
                apply_button.set_enabled(False)
        dialog.open()

    async def after_refactor(self, new_rel: str | None) -> None:
        """Reload what the change touched: this page, the page list, the tests and the open test."""
        rel = new_rel or self.rel
        self.dirty = False
        if rel:
            await self.open(rel)
        self.list_view.refresh()
        self.gen.tests.load_refs()
        self.gen.tests.reload_list()
        if self.gen.tests.file is not None:
            await self.gen.tests.open(self.gen.tests.file, self.gen.tests.index or 0)

    def rename_page_dialog(self) -> None:
        if not self._ready_for_refactor():
            return
        system, old = self.gen.system, self._page_name()
        holder: dict = {}

        def inputs() -> None:
            with labelled("New page name", "rn-page"):
                holder["name"] = ui.input(value=old).props('for="rn-page" data-testid="rename-new"') \
                    .classes("w-full mono-input")

        def make_plan() -> Plan:
            r = Refactor(self.svc.pom, self.svc.tests)
            r.rename_page(system, old, str(holder["name"].value or ""))
            return r.plan()

        def reopen() -> str:
            new = str(holder["name"].value).strip().removesuffix(".json")
            folder = self.rel.rpartition("/")[0] if self.rel and "/" in self.rel else ""
            return f"{folder}/{new}.json" if folder else f"{new}.json"

        self.refactor_dialog(f"Rename page {old}", "The file is renamed and every step that uses the page, in "
                             "tests and common actions, is updated.", inputs, make_plan, "page.rename",
                             f"{system}/{old}", reopen, "Rename")

    def rename_field_dialog(self) -> None:
        if not self._ready_for_refactor():
            return
        system, page = self.gen.system, self._page_name()
        names = [r.name for r in self.rows if r.name in self.orig_names]
        if not names:
            ui.notify("This page has no saved fields to rename.", type="warning")
            return
        holder: dict = {}

        def inputs() -> None:
            with ui.element("div").classes("any-form-row"):
                with labelled("Field", "rf-old").classes("is-grow"):
                    holder["old"] = ui.select(names, value=names[0]).props('for="rf-old"').classes("w-full")
                with labelled("New name", "rf-new").classes("is-grow"):
                    holder["new"] = ui.input().props('for="rf-new" data-testid="rename-new"') \
                        .classes("w-full mono-input")

        def make_plan() -> Plan:
            r = Refactor(self.svc.pom, self.svc.tests)
            r.rename_field(system, page, str(holder["old"].value), str(holder["new"].value or ""))
            return r.plan()

        self.refactor_dialog(f"Rename a field on {page}", "The field is renamed on the page and in every step "
                             "that uses it, in tests and common actions.", inputs, make_plan, "field.rename",
                             f"{system}/{page}", apply_label="Rename")

    def tidy_dialog(self) -> None:
        if not self._ready_for_refactor():
            return
        system, page = self.gen.system, self._page_name()
        findings = tidy_analyze(self.svc.pom, self.svc.tests, system, page)
        boxes: dict[str, object] = {}

        def inputs() -> None:
            if not findings:
                ui.label("Nothing to tidy: no duplicates, unused fields or redundant settings.") \
                    .classes("any-muted text-sm")
            for f in findings:
                with ui.element("div").classes("flex flex-col gap-1 w-full"):
                    if f.fixable:
                        boxes[f.id] = ui.checkbox(f.title, value=f.default).props(f'data-testid="tidy-{f.kind}"')
                    else:
                        text("div", f.title, "any-warn-line")
                    text("div", f.detail, "any-muted text-xs")

        def make_plan() -> Plan:
            chosen = [f for f in findings if f.id in boxes and boxes[f.id].value]
            if not chosen:
                raise RefactorError("Tick at least one change.")
            return tidy_plan(self.svc.pom, self.svc.tests, system, page, chosen)

        self.refactor_dialog(f"Tidy up {page}", "Pick the changes, preview them, then apply. Nothing is written "
                             "until you apply, and a failure leaves every file as it was.", inputs, make_plan,
                             "page.tidy", f"{system}/{page}", apply_label="Apply changes")

    def field_dialog(self, row: FieldRow) -> None:
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[36rem]"):
            text("h2", f"Field '{row.name}' - all keys", "any-title")
            ui.label("role, name, nested, tableId, waitForNewTab, focus, placeholder, label, text, contains, ...") \
                .classes("any-muted text-xs")
            code = ui.codemirror(json.dumps(row.cfg, indent=2, ensure_ascii=False), language="JSON") \
                .classes("any-code h-72")
            indexed = ui.switch("Repeats with the step's index (indexed)", value=bool(row.cfg.get("indexed"))) \
                .tooltip("A step with \"index\": N uses N as this field's index: one set of fields for every "
                         "row or section")

            def apply():
                try:
                    cfg = json.loads(code.value)
                    if not isinstance(cfg, dict):
                        raise ValueError("must be a JSON object")
                except ValueError as e:
                    ui.notify(f"Invalid JSON: {e}", type="negative")
                    return
                if indexed.value:
                    cfg["indexed"] = True
                else:
                    cfg.pop("indexed", None)
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

    async def save(self) -> None:
        data = self._current()
        if data is None or not guard("edit", "save page", self.rel or ""):
            return
        gone = [n for n in self.orig_names if n not in (data.get("fields") or {})]
        broken = [n for n in gone if self.usage and self.usage.field_uses(self._page_name(), n)]
        if broken and not await confirm(
                f"{', '.join(broken)} {'is' if len(broken) == 1 else 'are'} used by tests but no longer on this page. "
                "Renaming or removing a field here does not update those tests (use Maintain > Rename a field). "
                "Save anyway?", "Save anyway", "negative"):
            return
        try:
            self.hash = self.svc.pom.save(self.gen.system, self.rel, data, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(f"Not saved: {e}", type="negative", multi_line=True, timeout=8000)
            return
        self._set_model(data)
        self.list_view.refresh()
        self.gen.tests.load_refs()
        audit("page.save", f"{self.gen.system}/{self.rel}")
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
                if not guard("edit", "create page"):
                    return
                try:
                    rel = self.svc.pom.create(system, folder.value or "", name.value or "")
                    audit("page.create", f"{system}/{rel}")
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
                with ui.element("div").classes("any-head-right"):
                    with ui.element("div").classes("any-recorder"):
                        self.recorder_switch = ui.switch("Recorder", value=self.svc.recorder_enabled(),
                                                         on_change=self._toggle_recorder) \
                            .props('data-testid="recorder-switch"') \
                            .tooltip("Record tests in a real browser (Playwright codegen). Saved to .env.")
                        self.record_button = ui.button("Record", icon=icon("circle-play"), color=None,
                                                       on_click=lambda: guard("record", "record") and
                                                       open_recorder(self)) \
                            .props('flat unelevated no-caps data-testid="record-button"').classes("any-btn is-38")
                        self.record_button.set_visibility(bool(self.recorder_switch.value))
                    with ui.tabs().props('no-caps inline-label indicator-color="transparent" '
                                         'aria-label="Generator sections"').classes("any-subtabs") as tabs:
                        tests_tab = ui.tab("Test cases", icon=icon("list-checks"))
                        pages_tab = ui.tab("Pages", icon=icon("app-window"))
                self.sections, self.pages_tab, self.tests_tab = tabs, pages_tab, tests_tab
            with ui.tab_panels(tabs, value=tests_tab).classes("w-full bg-transparent"):
                with ui.tab_panel(tests_tab).classes("p-0"):
                    self.tests = TestEditor(self)
                with ui.tab_panel(pages_tab).classes("p-0"):
                    self.pages = PageEditor(self)
        self.tests.load_refs()

    async def open_page(self, name: str) -> None:
        """Jump to a page in the Pages tab (from a step)."""
        rel = self.svc.pom.screen_index(self.system).get(name) if self.system else None
        if not rel:
            ui.notify(f"Page {name!r} not found in Pages/{self.system}.", type="warning")
            return
        self.sections.set_value(self.pages_tab)
        await self.pages.open(rel)

    async def open_test(self, rel: str, index: int) -> None:
        """Jump to a test case in the Test cases tab (from a page's used-by list)."""
        self.sections.set_value(self.tests_tab)
        await self.tests.open(rel, index)

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

    def set_ai(self, enabled: bool) -> None:
        self.tests.draft_button.set_visibility(enabled)

    def _toggle_recorder(self, e) -> None:
        if not guard("settings", "turn the recorder on or off"):
            self.recorder_switch.set_value(not e.value)
            return
        audit("setting", "recorderEnabled", value=bool(e.value))
        try:
            self.svc.env.save({"recorderEnabled": bool(e.value)})
        except (ValueError, OSError) as err:
            ui.notify(f"Could not save the setting to .env: {err}", type="negative")
            return
        self.record_button.set_visibility(bool(e.value))
        ui.notify("Recorder on: use Record to capture a test" if e.value else "Recorder off", type="info")

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
