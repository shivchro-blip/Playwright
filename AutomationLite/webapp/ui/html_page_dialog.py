"""Generator: build a page's fields and its first test step from pasted HTML (no browser, no login)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services import html_page, page_conventions
from webapp.state import services
from webapp.ui.components import _describe, copy_button, icon_button, text
from webapp.ui.components import field as labelled
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import PageEditor

MAX_HTML = 2_000_000  # characters; a page's compact HTML is a few thousand


def renamed(form_data: dict, names: dict[str, str]) -> dict:
    """The formData with each field under the name the person gave it in the dialog."""
    return {names.get(key, key): value for key, value in form_data.items()}


def open_html_page(editor: PageEditor) -> None:
    from webapp.ui.generator import FieldRow, FormRow, StepModel, value_kind, value_text  # editor imports this module

    svc = services()
    gen = editor.gen
    system = gen.system
    if not system:
        ui.notify("Pick a system first", type="warning")
        return
    present = {r.name for r in editor.rows}
    state: dict = {"draft": None, "rows": [], "notes": [], "screen": ""}

    with ui.dialog().props("persistent") as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Add fields from pasted HTML"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Build", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", "Page from HTML", "any-title any-details-name")
                    text("span", editor._page_name(), "any-tc-code")
            icon_button("x", "Close", dialog.close)
        ui.label("Paste the page's HTML (tools/compact_html.py makes it short). The fields, their locators and autoGen "
                 "rules, and the step that fills the page are built from it, the way this site's pages are built. "
                 "Nothing is sent anywhere and no browser or login is needed.").classes("any-muted text-sm")
        with labelled("Page HTML", "html-in"):
            html_in = ui.textarea(placeholder="<div id=\"controls-div\"> ...") \
                .props('for="html-in" data-testid="html-input" outlined autogrow input-style="max-height:14rem" '
                       'spellcheck="false"').classes("w-full mono-input")
        with ui.element("div").classes("any-dialog-actions is-start"):
            build_button = ui.button("Build", icon=icon("sparkles"), color=None) \
                .props('data-testid="html-build"').classes("any-primary")
            ui.label("Conventions: Data/pageConventions.json, else the built-in profile the HTML looks like.") \
                .classes("any-muted text-xs")
        result = ui.element("div").classes("flex flex-col gap-2 w-full")

        def chosen_names(draft: html_page.PageDraft) -> dict[str, str]:
            return {orig: (name.value or orig).strip() or orig for orig, _, _, name, _ in state["rows"]}

        def ticked_in_step() -> set[str]:
            return {orig for orig, _, _, _, step in state["rows"] if step.value}

        @ui.refreshable
        def steps_panel() -> None:
            draft: html_page.PageDraft = state["draft"]
            screen = state.get("screen", "")
            if draft is None:
                return
            names = chosen_names(draft)
            for title, kwargs in (("Step: what the page requires, each question answered No",
                                   {"names": ticked_in_step()}),
                                  ("Step: every question answered Yes, what that reveals filled in",
                                   {"prefer": "Yes", "include_hidden": True})):
                steps = draft.steps(screen=screen or editor._page_name(), **kwargs)
                steps[0]["formData"] = renamed(steps[0]["formData"], names)
                source = json.dumps(steps, indent=2, ensure_ascii=False)
                with ui.element("div").classes("flex flex-col gap-1 w-full"):
                    with ui.element("div").classes("flex items-center gap-2"):
                        text("div", title, "any-eyebrow")
                        copy_button(source, f"Copy: {title}")
                    ui.code(source, language="json").classes("w-full").props('data-testid="html-steps"')

        def render(draft: html_page.PageDraft, notes: list[str]) -> None:
            result.clear()
            state.update(draft=draft, rows=[], notes=notes)
            with result:
                if not draft.fields:
                    with ui.element("div").classes("any-error-box"):
                        text("div", "Nothing to build", "any-eyebrow")
                        text("pre", "\n".join(draft.notes + notes) or "No fields were found in this HTML.",
                             "any-pre is-error")
                    return
                text("div", f"{len(draft.fields)} fields found - conventions: {draft.profile}", "any-eyebrow") \
                    .props('data-testid="html-found"')
                all_notes = notes + draft.notes
                if all_notes:
                    with ui.element("div").classes("flex flex-col gap-1 w-full min-w-0") \
                            .props('data-testid="html-notes"'):
                        for note in all_notes:
                            ui.label(note).classes("any-muted text-sm w-full") \
                                .style("white-space: normal; overflow-wrap: anywhere")
                with ui.element("div").classes("flex items-end gap-3 flex-wrap"):
                    with labelled("Page name used in the steps", "html-screen"):
                        screen = ui.input(value=editor._page_name() or draft.page_name) \
                            .props('for="html-screen" data-testid="html-screen"').classes("w-72 mono-input")
                    ui.label("Add: puts the field on this page. Step: includes it in the first step below.") \
                        .classes("any-muted text-xs pb-2")
                default = draft.default_names()
                with ui.element("div").classes("any-scan-list").props('data-testid="html-fields"'):
                    with ui.element("div").classes("any-scan-row any-muted text-xs"):
                        ui.label("Add").classes("w-10")
                        ui.label("Step").classes("w-10")
                        ui.label("Field").classes("w-56")
                    for f in draft.fields:
                        here = f.name in present
                        with ui.element("div").classes("any-scan-row"):
                            add = ui.checkbox(value=not here).props(f'aria-label="Add {f.name} to the page"')
                            add.set_enabled(not here)
                            step = ui.checkbox(value=f.name in default).props(f'aria-label="{f.name} in the step"')
                            step.on_value_change(lambda: steps_panel.refresh())
                            name = ui.input(value=f.name).props('aria-label="Field name" dense') \
                                .classes("w-56 mono-input")
                            name.on_value_change(lambda: steps_panel.refresh())
                            text("span", f.cfg.get("type", ""), "any-tag")
                            with ui.element("div").classes("min-w-0 flex-1"):
                                text("div", _describe({k: v for k, v in f.cfg.items() if k in
                                                       ("locator", "index")}), "any-small mono truncate")
                                extra = ", ".join(f"{k}={v}" for k, v in f.cfg.items() if k in ("autoGen", "typeKeys"))
                                flags = [x for x in (f.label[:60], extra, "required" if f.required else "",
                                                     "hidden when captured" if f.hidden else "",
                                                     "already on the page" if here else "") if x]
                                text("div", " - ".join(flags), "any-muted text-xs truncate")
                        state["rows"].append((f.name, f, add, name, step))
                state["screen"] = screen.value
                steps_panel()
                screen.on_value_change(lambda e: (state.update(screen=e.value or ""), steps_panel.refresh()))
                with ui.element("div").classes("any-dialog-actions"):
                    ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                    ui.button("Add steps to the open test", icon=icon("list-plus"), color=None,
                              on_click=lambda: add_to_test(state["screen"])) \
                        .props('data-testid="html-add-test"').classes("any-btn is-38")
                    ui.button("Add selected fields to this page", icon=icon("plus"), color=None, on_click=add_fields) \
                        .props('data-testid="html-add"').classes("any-primary")

        def add_fields() -> None:
            if not guard("edit", "add fields from HTML", system=system):
                return
            taken = {r.name for r in editor.rows}
            added: list[str] = []
            for orig, f, add, name, _ in state["rows"]:
                if not add.value or not add.enabled:
                    continue
                base = (name.value or orig).strip() or orig
                final, n = base, 2
                while final in taken:
                    final, n = f"{base}{n}", n + 1
                taken.add(final)
                editor.rows.append(FieldRow(final, dict(f.cfg)))
                added.append(final)
            if not added:
                ui.notify("Tick the fields to add.", type="warning")
                return
            editor.field_filter = ""
            editor.touch()
            editor.fields_view.refresh()
            audit("page.html", f"{system}/{editor.rel}", added=len(added))
            dialog.close()
            ui.notify(f"Added {len(added)} field(s); review them and Save the page.", type="positive")

        def add_to_test(screen: str) -> None:
            tests = gen.tests
            if tests.file is None:
                ui.notify("Open a test case in Test cases first; its steps are added to it.", type="warning")
                return
            draft: html_page.PageDraft = state["draft"]
            names = chosen_names(draft)
            steps = draft.steps(screen=screen or editor._page_name(), names=ticked_in_step())
            form = renamed(steps[0]["formData"], names)
            rows = [FormRow(k, value_kind(v), value_text(v, value_kind(v))) for k, v in form.items()]
            tests.steps.append(StepModel("screen", steps[0]["screen"], rows, True))
            if len(steps) > 1:
                tests.steps.append(StepModel("commonAction", steps[1]["commonAction"]))
            tests.load_refs()
            tests.touch()
            tests.steps_view.refresh()
            audit("testcase.html_steps", f"{system}/{tests.file}", fields=len(rows))
            dialog.close()
            ui.notify("Steps added to the open test case; save the page first so the step can find it, "
                      "then review and Save the test.", type="positive", multi_line=True)

        def build() -> None:
            source = html_in.value or ""
            if not source.strip():
                ui.notify("Paste the page's HTML first.", type="warning")
                return
            if len(source) > MAX_HTML:
                ui.notify("That is too much HTML for one page; compact it first (tools/compact_html.py).",
                          type="negative")
                return
            profile, notes = page_conventions.load(svc.settings.workspace, system, source)
            try:
                draft = html_page.build(source, profile)
            except html_page.HtmlError as e:
                ui.notify(str(e), type="negative", multi_line=True)
                return
            render(draft, notes)

        build_button.on_click(build)
    dialog.open()


__all__ = ["open_html_page", "renamed"]
