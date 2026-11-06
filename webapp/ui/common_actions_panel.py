"""Generator: Common actions tab. A common action is a list of steps that many test cases call by name
(Tests/com/*.json); this edits them, shows who calls each one, and renames or deletes them safely."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.json_files import ConflictError, PathError
from webapp.state import services
from webapp.ui.components import icon_button, styled_table, text
from webapp.ui.history_dialog import open_file_history
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import GeneratorPanel

SAVE_ERRORS = (ValueError, ConflictError, PathError, OSError)


class CommonActionEditor:
    def __init__(self, gen: GeneratorPanel):
        self.gen = gen
        self.svc = services()
        self.name: str | None = None
        self.file = ""
        self.hash: str | None = None
        self.dirty = False
        self.source = ""

        with ui.element("div").classes("any-split"):
            with ui.element("section").classes("any-card any-split-list").props('aria-label="Common actions"'):
                with ui.element("div").classes("any-list-head"):
                    self.search = ui.input(placeholder="Search common actions") \
                        .props('clearable aria-label="Search common actions"').classes("flex-1")
                    icon_button("plus", "New common action", self.new_dialog).props('data-testid="common-new"')
                    icon_button("rotate-cw", "Reload", lambda: self.list_view.refresh())
                self.list_view()
            with ui.element("section").classes("any-card any-split-main").props('aria-label="Common action editor"'):
                self.editor()

    # ───── list ─────

    @ui.refreshable_method
    def list_view(self) -> None:
        rows = [{"name": a.name, "file": a.file, "steps": a.steps, "used": a.used_by} for a in self.svc.common.list()]
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="name", pagination=15, columns=[
                {"name": "name", "label": "Common action", "field": "name", "align": "left", "sortable": True},
                {"name": "steps", "label": "Steps", "field": "steps", "align": "right", "sortable": True},
                {"name": "used", "label": "Called by", "field": "used", "align": "right", "sortable": True},
            ]).classes("w-full is-clickable is-compact")
            styled_table(table, len(rows))
            table.add_slot("body-cell-name", '<q-td :props="props"><span class="any-tc-name">{{ props.value }}</span>'
                                             '<div class="any-tc-code">{{ props.row.file }}</div></q-td>')
            table.add_slot("body-cell-steps", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.add_slot("body-cell-used", '<q-td :props="props" class="any-num">'
                                             '<span v-if="props.value">{{ props.value }}</span>'
                                             '<span v-else class="any-muted">unused</span></q-td>')
            table.add_slot("no-data", '<div class="any-muted text-sm">No common actions yet - use + to create one. '
                                      'They live in Tests/com.</div>')
            self.search.bind_value(table, "filter")
            table.on("rowClick", lambda e: self.open(e.args[1]["name"]))

    # ───── load ─────

    async def open(self, name: str) -> None:
        from webapp.ui.generator import confirm
        if self.dirty and not await confirm("Discard unsaved changes to this common action?", "Discard", "negative"):
            return
        try:
            action, file, sha = self.svc.common.load(name)
        except SAVE_ERRORS as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        self.name, self.file, self.hash = name, file, sha
        self.source = json.dumps(action, indent=2, ensure_ascii=False)
        self.dirty = False
        self.editor.refresh()

    def touch(self) -> None:
        if not self.dirty:
            self.dirty = True
            self.dirty_badge.set_visibility(True)

    # ───── editor ─────

    @ui.refreshable_method
    def editor(self) -> None:
        if self.name is None:
            ui.label("Select a common action on the left, or create one with +.").classes("any-empty any-muted")
            return
        users = self.svc.common.used_by(self.name)
        with ui.element("div").classes("any-editor-head"):
            with ui.element("div").classes("any-editor-title"):
                text("h2", self.name, "any-title")
                text("span", f"Tests/com/{self.file}", "any-tc-code")
                self.dirty_badge = text("span", "unsaved", "any-pill pill-warn")
                self.dirty_badge.set_visibility(self.dirty)
            with ui.element("div").classes("any-toolbar"):
                ui.button("Check", icon=icon("list-checks"), color=None, on_click=lambda: self.check(True)) \
                    .props('data-testid="common-check"').classes("any-btn is-38")
                ui.button("Rename", icon=icon("tag"), color=None, on_click=self.rename_dialog) \
                    .props('data-testid="common-rename"').classes("any-btn is-38")
                icon_button("history", "Earlier versions of this file", self.open_history) \
                    .props('data-testid="common-history"')
                icon_button("trash-2", "Delete common action", self.delete).classes("is-danger")
                ui.button("Revert", icon=icon("undo-2"), color=None, on_click=self.revert).classes("any-btn is-38")
                ui.button("Save", icon=icon("save"), color=None, on_click=self.save) \
                    .props('data-testid="common-save"').classes("any-primary")
        if users:
            calls = sorted({u.rsplit(" step ", 1)[0] for u in users})
            ui.label(f"Called {len(users)} time{'s' if len(users) != 1 else ''} by " + ", ".join(calls[:6])
                     + (f" and {len(calls) - 6} more" if len(calls) > 6 else "")).classes("any-muted text-sm") \
                .props('data-testid="common-used"')
        else:
            text("span", "Not called by any test case", "any-pill pill-warn")
        try:
            action = json.loads(self.source)
        except ValueError:
            action = {}
        params = self.svc.common.parameters(action)
        if params:
            with ui.expansion(f"Values a test can change ({len(params)})", icon=icon("braces")) \
                    .classes("any-disclosure").props('data-testid="common-params"'):
                ui.label('Give a step that calls this action  "override": "<page>|<field>|<value>"  to use another '
                         'value for one call, and  "loop": N  to repeat it.').classes("any-muted text-xs")
                for p in params:
                    with ui.element("div").classes("any-scan-row"):
                        text("span", p.override, "mono")
                        text("span", f"now {p.value}", "any-muted text-xs")
        ui.label('Steps run in order, like the steps of a test case: {"screen": "<page name>", "formData": '
                 '{"<field>": "<value>"}}.').classes("any-muted text-xs")
        self.code = ui.codemirror(self.source, language="JSON", line_wrapping=True).classes("any-code h-[55vh]") \
            .props('data-testid="common-code"')
        self.code.on_value_change(lambda: self.touch())

    def _current(self) -> dict | None:
        try:
            action = json.loads(self.code.value)
        except ValueError as e:
            ui.notify(f"Not valid JSON: {e}", type="negative", multi_line=True)
            return None
        return action

    def check(self, notify: bool = True) -> bool:
        action = self._current()
        if action is None:
            return False
        result = self.svc.common.check(action)
        if notify:
            if result.errors:
                ui.notify("\n".join(result.errors), type="negative", multi_line=True)
            elif result.warnings:
                ui.notify("\n".join(result.warnings), type="warning", multi_line=True, timeout=8000)
            else:
                ui.notify("No problems found", type="positive")
        return not result.errors

    def save(self) -> None:
        action = self._current()
        if action is None or not guard("edit", "save common action", self.name or ""):
            return
        try:
            self.hash = self.svc.common.save(self.name, action, self.hash)
        except SAVE_ERRORS as e:
            ui.notify(f"Not saved: {e}", type="negative", multi_line=True, timeout=8000)
            return
        self.source = json.dumps(action, indent=2, ensure_ascii=False)
        self.dirty = False
        audit("commonaction.save", self.name or "", file=self.file)
        self.list_view.refresh()
        self.editor.refresh()
        ui.notify(f"Saved {self.name}", type="positive")

    async def revert(self) -> None:
        if self.name is not None:
            self.dirty = False
            await self.open(self.name)

    def open_history(self) -> None:
        if self.name is None:
            return

        async def reload() -> None:
            self.dirty = False
            self.list_view.refresh()
            try:
                await self.open(self.name)
            except Exception:
                self.name = None
                self.editor.refresh()

        open_file_history(f"Tests/com/{self.file}", self.svc.common.path_of(self.name), reload)

    async def delete(self) -> None:
        from webapp.ui.generator import confirm
        if not guard("delete", "delete common action", self.name or ""):
            return
        if not await confirm(f"Delete the common action '{self.name}'? Earlier versions of its file stay in the "
                             "history.", "Delete", "negative"):
            return
        try:
            self.svc.common.delete(self.name)
        except SAVE_ERRORS as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("commonaction.delete", self.name or "")
        self.name = self.hash = None
        self.dirty = False
        self.list_view.refresh()
        self.editor.refresh()

    async def rename_dialog(self) -> None:
        if self.name is None or not guard("edit", "rename common action", self.name):
            return
        old = self.name
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[30rem]"):
            text("h2", f"Rename {old}", "any-title")
            ui.label("Every test step that calls it is updated too (the old versions of those files stay in "
                     "their history).").classes("any-muted text-sm")
            new = ui.input(value=old).props('aria-label="New name" data-testid="common-rename-name"') \
                .classes("w-full")
            preview = ui.label("").classes("any-muted text-sm").props('data-testid="common-rename-preview"')

            def refresh_preview() -> None:
                try:
                    files = self.svc.common.rename_preview(old, new.value or "")
                    uses = len(self.svc.common.used_by(old))
                    preview.text = (f"{uses} call{'s' if uses != 1 else ''} in {', '.join(files)} will change."
                                    if uses else "Nothing calls it yet.")
                    preview.classes(replace="any-muted text-sm")
                    ok.set_enabled(bool(new.value) and new.value.strip() != old)
                except SAVE_ERRORS as e:
                    preview.text = str(e)
                    preview.classes(replace="text-sm text-negative")
                    ok.set_enabled(False)

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38")
                ok = ui.button("Rename", color=None, on_click=lambda: dialog.submit(True)) \
                    .props('data-testid="common-rename-ok"').classes("any-primary")
            new.on_value_change(refresh_preview)
            refresh_preview()
        if await dialog:
            if self.dirty:
                ui.notify("Save or revert your changes first.", type="warning")
            else:
                try:
                    count = self.svc.common.rename(old, (new.value or "").strip())
                except SAVE_ERRORS as e:
                    ui.notify(str(e), type="negative", multi_line=True)
                else:
                    audit("commonaction.rename", old, to=new.value, calls=count)
                    self.gen.tests.load_refs()
                    self.list_view.refresh()
                    await self.open((new.value or "").strip())
                    ui.notify(f"Renamed to {new.value} ({count} call{'s' if count != 1 else ''} updated)",
                              type="positive")
        dialog.delete()

    async def new_dialog(self) -> None:
        from webapp.ui.generator import confirm
        if self.dirty and not await confirm("Discard unsaved changes to this common action?", "Discard", "negative"):
            return
        files = sorted({a.file for a in self.svc.common.list()} | {"commonActions.json"})
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[28rem]"):
            text("h2", "New common action", "any-title")
            name = ui.input(placeholder="Login as applicant").props('aria-label="Name" data-testid="common-name"') \
                .classes("w-full")
            where = ui.select(files, value=files[0], label="File in Tests/com").classes("w-full")
            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")

                async def ok() -> None:
                    if not guard("edit", "create common action"):
                        return
                    try:
                        created = self.svc.common.create(name.value or "", where.value)
                    except SAVE_ERRORS as e:
                        ui.notify(str(e), type="negative")
                        return
                    audit("commonaction.create", created, file=where.value)
                    dialog.close()
                    self.list_view.refresh()
                    await self.open(created)

                ui.button("Create", color=None, on_click=ok).props('data-testid="common-create"') \
                    .classes("any-primary")
        dialog.open()
