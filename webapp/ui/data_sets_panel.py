"""Generator: Data sets tab. A data file holds the values a test types (addresses, names, numbers), one set per
row and one key per column; a test case with that data set runs once per set (see utils/data_sets.py)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nicegui import ui

from utils.data_sets import KEY_OK, NAME_OK, DataSetError, validate
from webapp.auth import audit, guard
from webapp.services.json_files import ConflictError, PathError
from webapp.state import services
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, styled_table, text
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import GeneratorPanel

SAVE_ERRORS = (ValueError, ConflictError, PathError, OSError)


@dataclass
class SetRow:
    name: str
    values: list[str] = field(default_factory=list)  # aligned with DataSetEditor.keys


def _text(value) -> str:
    return "" if value is None else str(value)


class DataSetEditor:
    def __init__(self, gen: GeneratorPanel):
        self.gen = gen
        self.svc = services()
        self.name: str | None = None
        self.hash: str | None = None
        self.description = ""
        self.keys: list[str] = []
        self.defaults: list[str] = []
        self.rows: list[SetRow] = []
        self.extra: dict = {}
        self.dirty = False
        self.json_mode = False

        with ui.element("div").classes("any-split"):
            with ui.element("section").classes("any-card any-split-list").props('aria-label="Data sets"'):
                with ui.element("div").classes("any-list-head"):
                    self.search = ui.input(placeholder="Search data sets") \
                        .props('clearable aria-label="Search data sets"').classes("flex-1")
                    icon_button("plus", "New data file", self.new_dialog).props('data-testid="data-new"')
                    icon_button("rotate-cw", "Reload", lambda: self.list_view.refresh())
                self.list_view()
            with ui.element("section").classes("any-card any-split-main").props('aria-label="Data set editor"'):
                self.editor()

    # ───── list ─────

    @ui.refreshable_method
    def list_view(self) -> None:
        rows = [{"name": r.name, "sets": r.sets, "keys": r.keys, "used": r.used_by}
                for r in self.svc.datasets.list()]
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="name", pagination=15, columns=[
                {"name": "name", "label": "Data file", "field": "name", "align": "left", "sortable": True},
                {"name": "sets", "label": "Sets", "field": "sets", "align": "right", "sortable": True},
                {"name": "used", "label": "Used by", "field": "used", "align": "right", "sortable": True},
            ]).classes("w-full is-clickable is-compact")
            styled_table(table, len(rows))
            table.add_slot("body-cell-name", '<q-td :props="props"><span class="any-tc-name">{{ props.value }}'
                                             '</span></q-td>')
            table.add_slot("body-cell-sets", '<q-td :props="props" class="any-num">'
                                             '{{ props.value < 0 ? "unreadable" : props.value }}</q-td>')
            table.add_slot("body-cell-used", '<q-td :props="props" class="any-num">'
                                             '<span v-if="props.value">{{ props.value }}</span>'
                                             '<span v-else class="any-muted">unused</span></q-td>')
            table.add_slot("no-data", '<div class="any-muted text-sm">No data files yet - use + to create one. '
                                      'They live in Data/TestData.</div>')
            self.search.bind_value(table, "filter")
            table.on("rowClick", lambda e: self.open(e.args[1]["name"]))

    # ───── load / build ─────

    async def open(self, name: str) -> None:
        from webapp.ui.generator import confirm
        if self.dirty and not await confirm("Discard unsaved changes to this data file?", "Discard", "negative"):
            return
        try:
            data, sha = self.svc.datasets.load(name)
            validate(data, name)
        except (ValueError, OSError) as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        self.name, self.hash = name, sha
        self._set_model(data)

    def _set_model(self, data: dict) -> None:
        defaults, sets = validate(data, self.name or "data")
        self.description = _text(data.get("description"))
        self.extra = {k: v for k, v in data.items() if k not in ("description", "defaults", "sets")}
        keys: dict[str, None] = dict.fromkeys(defaults)
        for values in sets.values():
            keys.update(dict.fromkeys(values))
        self.keys = list(keys)
        self.defaults = [_text(defaults.get(k)) for k in self.keys]
        self.rows = [SetRow(n, [_text(v.get(k)) for k in self.keys]) for n, v in sets.items()]
        self.dirty = False
        self.editor.refresh()

    def build(self) -> dict:
        names = [r.name.strip() for r in self.rows]
        if not names:
            raise ValueError("Add at least one set.")
        for n in names:
            if not NAME_OK.match(n):
                raise ValueError(f"Set name {n!r}: letters, digits, '_' or '-' only.")
        if len(set(names)) != len(names):
            raise ValueError("Two sets have the same name.")
        keys = [k.strip() for k in self.keys]
        for k in keys:
            if not KEY_OK.match(k):
                raise ValueError(f"Key {k!r}: letters, digits, '_' or '-', starting with a letter or '_'.")
        if len(set(keys)) != len(keys):
            raise ValueError("Two columns have the same key.")

        def pick(values: list[str]) -> dict:
            return {k: v for k, v in zip(keys, values) if v != ""}  # an empty cell means "not set here"

        data: dict = {"description": self.description, "defaults": pick(self.defaults),
                      "sets": {n: pick(r.values) for n, r in zip(names, self.rows)}}
        return {**data, **self.extra}

    def touch(self) -> None:
        if not self.dirty:
            self.dirty = True
            self.dirty_badge.set_visibility(True)

    # ───── editor ─────

    @ui.refreshable_method
    def editor(self) -> None:
        if self.name is None:
            ui.label("Select a data file on the left, or create one with +.").classes("any-empty any-muted")
            return
        users = self.svc.datasets.used_by(self.name)
        with ui.element("div").classes("any-editor-head"):
            with ui.element("div").classes("any-editor-title"):
                text("h2", self.name, "any-title")
                text("span", f"Data/TestData/{self.name}.json", "any-tc-code")
                self.dirty_badge = text("span", "unsaved", "any-pill pill-warn")
                self.dirty_badge.set_visibility(self.dirty)
            with ui.element("div").classes("any-toolbar"):
                ui.toggle({False: "Form", True: "JSON"}, value=self.json_mode, on_change=self.toggle_json) \
                    .props("no-caps unelevated").classes("any-segmented")
                icon_button("trash-2", "Delete data file", self.delete).classes("is-danger")
                ui.button("Revert", icon=icon("undo-2"), color=None, on_click=self.revert).classes("any-btn is-38")
                ui.button("Save", icon=icon("save"), color=None, on_click=self.save) \
                    .props('data-testid="data-save"').classes("any-primary")
        if users:
            ui.label("Used by " + ", ".join(users[:6]) + (f" and {len(users) - 6} more" if len(users) > 6 else "")) \
                .classes("any-muted text-sm")
        else:
            text("span", "Not used by any test case", "any-pill pill-warn")
        if self.json_mode:
            try:
                source = json.dumps(self.build(), indent=2, ensure_ascii=False)
            except ValueError as e:
                source = f"// Fix the form first: {e}"
            self.code = ui.codemirror(source, language="JSON", line_wrapping=True).classes("any-code h-[60vh]")
            self.code.on_value_change(lambda: self.touch())
            return
        with labelled("Description", "ds-desc"):
            ui.input(value=self.description, on_change=self._set_description).props('for="ds-desc"') \
                .classes("w-full")
        ui.label("One row per set, one column per key. A test case with this data set runs once per row and "
                 "uses a value with DATA.<key>. An empty cell falls back to the defaults row.") \
            .classes("any-muted text-xs")
        self.grid()

    def _set_description(self, e) -> None:
        self.description = e.value or ""
        self.touch()

    @ui.refreshable_method
    def grid(self) -> None:
        columns = f"11rem repeat({len(self.keys)}, minmax(7.5rem, 1fr)) 4.5rem"
        with ui.element("div").classes("any-grid-scroll").props('data-testid="data-grid"'):
            with ui.element("div").classes("any-data-grid is-head").style(f"grid-template-columns: {columns}"):
                ui.label("Set")
                for i, key in enumerate(self.keys):
                    with ui.element("div").classes("flex items-center"):
                        ui.input(value=key, on_change=lambda e, i=i: self._set_key(i, e.value)) \
                            .props('aria-label="Key" dense').classes("mono-input flex-1")
                        icon_button("x", f"Remove column {key}", lambda i=i: self.remove_column(i), "is-24") \
                            .classes("is-danger")
                ui.label("")
            with ui.element("div").classes("any-data-grid is-defaults").style(f"grid-template-columns: {columns}"):
                text("span", "defaults (every set)", "any-muted text-sm")
                for i, value in enumerate(self.defaults):
                    ui.input(value=value, on_change=lambda e, i=i: self._set_default(i, e.value)) \
                        .props('aria-label="Default" dense')
                ui.label("")
            for row in self.rows:
                with ui.element("div").classes("any-data-grid").style(f"grid-template-columns: {columns}"):
                    ui.input(value=row.name, on_change=lambda e, r=row: self._set_name(r, e.value)) \
                        .props('aria-label="Set name" dense').classes("mono-input")
                    for i, value in enumerate(row.values):
                        ui.input(value=value, on_change=lambda e, r=row, i=i: self._set_cell(r, i, e.value)) \
                            .props(f'aria-label="{self.keys[i]} of {row.name}" dense')
                    with ui.element("div").classes("flex"):
                        icon_button("copy", f"Duplicate set {row.name}", lambda r=row: self.duplicate_set(r), "is-24")
                        icon_button("x", f"Remove set {row.name}", lambda r=row: self.remove_set(r), "is-24") \
                            .classes("is-danger")
        with ui.element("div").classes("any-form-line is-actions"):
            ui.button("Set", icon=icon("plus"), color=None, on_click=self.add_set) \
                .props('flat data-testid="data-add-set"').classes("any-ghost")
            ui.button("Column", icon=icon("plus"), color=None, on_click=self.add_column) \
                .props('flat data-testid="data-add-column"').classes("any-ghost")

    # cell edits only touch the model: refreshing would drop the focus from the box being typed in
    def _set_key(self, i: int, value) -> None:
        self.keys[i] = value or ""
        self.touch()

    def _set_default(self, i: int, value) -> None:
        self.defaults[i] = value or ""
        self.touch()

    def _set_name(self, row: SetRow, value) -> None:
        row.name = value or ""
        self.touch()

    def _set_cell(self, row: SetRow, i: int, value) -> None:
        row.values[i] = value or ""
        self.touch()

    def add_set(self) -> None:
        n = len(self.rows) + 1
        taken = {r.name for r in self.rows}
        while f"set{n}" in taken:
            n += 1
        self.rows.append(SetRow(f"set{n}", [""] * len(self.keys)))
        self.touch()
        self.grid.refresh()

    def duplicate_set(self, row: SetRow) -> None:
        taken, name, n = {r.name for r in self.rows}, f"{row.name}-copy", 2
        while name in taken:
            name, n = f"{row.name}-copy{n}", n + 1
        self.rows.insert(self.rows.index(row) + 1, SetRow(name, list(row.values)))
        self.touch()
        self.grid.refresh()

    def remove_set(self, row: SetRow) -> None:
        self.rows.remove(row)
        self.touch()
        self.grid.refresh()

    def add_column(self) -> None:
        n = len(self.keys) + 1
        while f"key{n}" in self.keys:
            n += 1
        self.keys.append(f"key{n}")
        self.defaults.append("")
        for row in self.rows:
            row.values.append("")
        self.touch()
        self.grid.refresh()

    def remove_column(self, i: int) -> None:
        del self.keys[i], self.defaults[i]
        for row in self.rows:
            del row.values[i]
        self.touch()
        self.grid.refresh()

    # ───── commands ─────

    def _current(self) -> dict | None:
        try:
            data = json.loads(self.code.value) if self.json_mode else self.build()
            validate(data, self.name or "data")
            return data
        except (ValueError, DataSetError) as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return None

    def toggle_json(self, e) -> None:
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
        if data is None or not guard("edit", "save data set", self.name or ""):
            return
        try:
            self.hash = self.svc.datasets.save(self.name, data, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(f"Not saved: {e}", type="negative", multi_line=True, timeout=8000)
            return
        self._set_model(data)
        self.list_view.refresh()
        audit("dataset.save", self.name or "")
        ui.notify(f"Saved {self.name}.json", type="positive")

    async def revert(self) -> None:
        if self.name is not None:
            self.dirty = False
            await self.open(self.name)

    async def delete(self) -> None:
        from webapp.ui.generator import confirm
        if not guard("delete", "delete data file", self.name or ""):
            return
        if not await confirm(f"Delete {self.name}.json? This cannot be undone.", "Delete", "negative"):
            return
        try:
            self.svc.datasets.delete(self.name, self.hash or "")
        except SAVE_ERRORS as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("dataset.delete", self.name or "")
        self.name = self.hash = None
        self.dirty = False
        self.list_view.refresh()
        self.editor.refresh()

    async def new_dialog(self) -> None:
        from webapp.ui.generator import confirm
        if self.dirty and not await confirm("Discard unsaved changes to this data file?", "Discard", "negative"):
            return
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[28rem]"):
            text("h2", "New data file", "any-title")
            with labelled("Name", "dlg-data"):
                name = ui.input(placeholder="applicants").props('for="dlg-data" data-testid="data-new-name"') \
                    .classes("w-full mono-input")

            async def ok() -> None:
                if not guard("edit", "create data file"):
                    return
                try:
                    created = self.svc.datasets.create(name.value or "")
                except SAVE_ERRORS as e:
                    ui.notify(str(e), type="negative")
                    return
                audit("dataset.create", created)
                dialog.close()
                self.dirty = False
                self.list_view.refresh()
                await self.open(created)

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                ui.button("Create", color=None, on_click=ok).props('data-testid="data-create"') \
                    .classes("any-primary")
        dialog.open()
