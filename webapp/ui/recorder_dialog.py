"""Generator: record a test in a real browser (Playwright codegen), review it, save it as a page + test case."""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from nicegui import ui

from webapp.services.json_files import ConflictError, PathError
from webapp.services.recorder import (
    DEFAULT_BREAKS, RecordOptions, Recording, RecorderError, save_recording, save_wizard, suggest_page_name,
)
from webapp.auth import audit, guard
from webapp.state import services
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import GeneratorPanel

NEW_FILE = "+ New file..."
SAVE_ERRORS = (RecorderError, ConflictError, PathError, ValueError, OSError)


def _describe(cfg: dict) -> str:
    keys = ("locator", "role", "name", "label", "placeholder", "text", "index")
    return "  ".join(f"{k}={cfg[k]!r}" for k in keys if cfg.get(k) not in (None, ""))


def open_recorder(gen: GeneratorPanel) -> None:
    svc = services()
    system = gen.system
    if not system:
        ui.notify("Pick a system first", type="warning")
        return
    blocker = svc.recorder_blocker()
    if blocker:
        ui.notify(blocker, type="warning", multi_line=True, timeout=8000)
        return
    envs = svc.systems.environments(system)
    env_default = "dev" if "dev" in envs else (envs[0] if envs else "")

    def base_url(env: str | None) -> str:
        login = svc.systems.login(system, env) if env else None
        return login.base_url if login else ""

    session_file = svc.settings.workspace / "Data" / "session.json"
    state: dict = {"recording": None, "env": env_default, "wizard": False, "breaks": ", ".join(DEFAULT_BREAKS),
                   "id_attr": "", "id_prefix": ""}

    with ui.dialog().props("persistent") as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Record a test"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Recorder", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", "Record a test", "any-title any-details-name")
                    text("span", system, "any-tc-code")

            def close() -> None:
                svc.recorder.cancel()
                timer.deactivate()
                dialog.close()

            icon_button("x", "Close", close)

        @ui.refreshable
        def body() -> None:
            session = svc.recorder.session if svc.recorder.active else None
            if state["recording"] is not None:
                review(state["recording"])
            elif session:
                recording_view()
            else:
                setup_view()

        def setup_view() -> None:
            ui.label("A Chromium window opens on this computer. Click through the flow you want to test, then close "
                     "that window: the steps become a page and a test case you review before saving.") \
                .classes("any-muted text-sm")
            with ui.element("div").classes("any-form-row"):
                if envs:
                    with labelled("Environment", "rec-env"):
                        ui.select(envs, value=state["env"], on_change=lambda e: (
                            state.update(env=e.value), setattr(url, "value", base_url(e.value)))) \
                            .props('for="rec-env"').classes("w-40")
                with labelled("Start URL", "rec-url").classes("is-grow"):
                    url = ui.input(value=base_url(state["env"])).props('for="rec-url"').classes("w-full mono-input")
            use_session = ui.switch("Start logged in (saved session, Data/session.json)", value=True) \
                if session_file.is_file() else None
            with ui.expansion("Multi-screen flows and site ids", icon=icon("list-checks")).classes("any-disclosure"):
                with ui.element("div").classes("any-disclosure-body"):
                    ui.switch("Split into one page per screen (wizard mode)", value=state["wizard"],
                              on_change=lambda e: state.update(wizard=e.value)).props('data-testid="rec-wizard"')
                    with labelled("A screen ends when one of these buttons is clicked", "rec-breaks"):
                        ui.input(value=state["breaks"], on_change=lambda e: state.update(breaks=e.value or "")) \
                            .props('for="rec-breaks"').classes("w-full")
                    with ui.element("div").classes("any-form-row"):
                        with labelled("ID attribute (optional)", "rec-id-attr").classes("is-grow"):
                            ui.input(value=state["id_attr"], placeholder="data-attribute-id",
                                     on_change=lambda e: state.update(id_attr=(e.value or "").strip())) \
                                .props('for="rec-id-attr"').classes("w-full mono-input")
                        with labelled("Remove this prefix from field names", "rec-id-prefix").classes("is-grow"):
                            ui.input(value=state["id_prefix"], placeholder="LSI_APP_",
                                     on_change=lambda e: state.update(id_prefix=(e.value or "").strip())) \
                                .props('for="rec-id-prefix"').classes("w-full mono-input")
                    ui.label("Elements that carry the ID attribute are located by it and named after it "
                             "(LSI_APP_TaxNotPaid becomes TaxNotPaid).").classes("any-muted text-xs")
            with ui.element("div").classes("any-dialog-actions is-start"):
                def start() -> None:
                    storage = session_file if use_session is not None and use_session.value else None
                    options = RecordOptions(
                        break_on=tuple(b.strip() for b in state["breaks"].split(",") if b.strip())
                        if state["wizard"] else (),
                        id_attribute=state["id_attr"], id_prefix=state["id_prefix"])
                    try:
                        svc.recorder.start((url.value or "").strip(), storage, options)
                    except RecorderError as e:
                        ui.notify(str(e), type="warning", multi_line=True)
                        return
                    body.refresh()
                ui.button("Start recording", icon=icon("circle-play"), color=None, on_click=start) \
                    .props('data-testid="rec-start"').classes("any-primary")

        def recording_view() -> None:
            with ui.element("div").classes("any-rec-live").props('role="status" data-testid="rec-live"'):
                ui.element("span").classes("any-rec-dot").props('aria-hidden="true"')
                with ui.element("div"):
                    text("div", "Recording", "any-rec-title")
                    ui.label("Use the Chromium window that opened on this computer. Close it when you are done; "
                             "the recording appears here.").classes("any-muted text-sm")
            with ui.element("div").classes("any-dialog-actions is-start"):
                ui.button("Cancel recording", icon=icon("square-solid"), color=None,
                          on_click=lambda: (svc.recorder.cancel(), body.refresh())) \
                    .props("flat unelevated no-caps").classes("any-btn is-38")

        def review(rec: Recording) -> None:
            with ui.element("div").classes("any-lc-summary"):
                counts = (("screens", len(rec.screens)),) if rec.screens else ()
                for label, n in (*counts, ("fields", rec.field_count), ("actions", rec.actions)):
                    with ui.element("span").classes("any-pill pill-running"):
                        ui.element("span").classes("dot").props('aria-hidden="true"')
                        text("span", f"{n} {label}")
                if rec.start_url:
                    text("code", rec.start_url, "any-lc-desc")
            for warning in rec.warnings:
                with ui.element("div").classes("any-callout is-inline").props('role="status"'):
                    ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
                    text("div", warning, "any-callout-text")
            groups = [(s.name, s.fields) for s in rec.screens] if rec.screens else [("", rec.fields)]
            for screen_name, fields in groups:
                if not fields:
                    continue
                if screen_name:
                    text("h3", f"{screen_name}: {len(fields)} field{'' if len(fields) == 1 else 's'}", "any-eyebrow")
                with ui.element("div").classes("any-table-wrap"):
                    rows = [{"name": n, "type": c.get("type", ""), "locator": _describe(c)}
                            for n, c in fields.items()]
                    ui.table(rows=rows, row_key="name", pagination=0, columns=[
                        {"name": "name", "label": "Field", "field": "name", "align": "left"},
                        {"name": "type", "label": "Type", "field": "type", "align": "left"},
                        {"name": "locator", "label": "Locator", "field": "locator", "align": "left"},
                    ]).props("flat square hide-bottom").classes("w-full any-table is-compact")
            steps_json = {s.name: s.steps[0] for s in rec.screens} if rec.screens else rec.steps
            with ui.expansion("Steps (formData)", icon=icon("braces")).classes("any-disclosure"):
                text("pre", json.dumps(steps_json, indent=2, ensure_ascii=False), "any-pre")
            with ui.expansion("Recorded code (credentials masked)", icon=icon("terminal")).classes("any-disclosure"):
                text("pre", rec.source, "any-pre")
            if not rec.field_count:
                with ui.element("div").classes("any-dialog-actions is-start"):
                    ui.button("Record again", icon=icon("rotate-cw"), color=None,
                              on_click=lambda: (state.update(recording=None), body.refresh())) \
                        .props("flat unelevated no-caps").classes("any-btn is-38")
                return
            save_form(rec)

        def save_form(rec: Recording) -> None:
            suggested = suggest_page_name(rec.start_url)
            pages = sorted(set(svc.pom.screen_index(system)) | {suggested}, key=str.casefold)
            wizard = bool(rec.screens)
            screen_inputs: list = []
            files = svc.tests.files(system)
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            text("h3", "Save as", "any-eyebrow")
            with ui.element("div").classes("any-form-row"):
                if wizard:
                    stem = suggested.removesuffix("Page")
                    with ui.element("div").classes("is-grow flex flex-col gap-2"):
                        text("div", "One page per screen (new, or an existing page to add fields to)", "any-label")
                        for i, screen in enumerate(rec.screens, 1):
                            n = len(screen.fields)
                            screen_inputs.append(
                                ui.input(value=f"{stem}_{screen.name}",
                                         label=f"Screen {i}: {n} field{'' if n == 1 else 's'}")
                                .props(f'data-testid="rec-screen-{i}"').classes("w-full mono-input"))
                else:
                    with labelled("Page (new, or an existing one to add fields to)", "rec-page").classes("is-grow"):
                        page = ui.select(pages, value=suggested, with_input=True,
                                         new_value_mode="add-unique").props('for="rec-page"').classes("w-full")
                with labelled("Test case ID", "rec-tc").classes("is-grow"):
                    test_id = ui.input(value=f"TC-{system}-REC-{stamp}").props('for="rec-tc"').classes("w-full")
            with ui.element("div").classes("any-form-row"):
                with labelled("Test file", "rec-file").classes("is-grow"):
                    file_sel = ui.select(files + [NEW_FILE], value=files[0] if files else NEW_FILE) \
                        .props('for="rec-file"').classes("w-full")
                with labelled("New file name", "rec-new-file").classes("is-grow") as new_box:
                    new_name = ui.input(value="RecordedTests.json").props('for="rec-new-file"').classes("w-full")
                new_box.bind_visibility_from(file_sel, "value", value=NEW_FILE)
                with labelled("Tags", "rec-tags").classes("is-grow"):
                    tags = ui.input(value=f"{system}, recorded").props('for="rec-tags"').classes("w-full")

            async def save() -> None:
                if not guard("record", "save recording"):
                    return
                try:
                    rel = svc.tests.normalize_file_name(new_name.value or "") if file_sel.value == NEW_FILE \
                        else file_sel.value
                    common = dict(base_url=base_url(state["env"]), test_id=test_id.value or "", test_file=rel,
                                  env=state["env"] or "",
                                  tags=[t.strip() for t in (tags.value or "").split(",") if t.strip()],
                                  description=f"Recorded {datetime.now():%Y-%m-%d %H:%M} from {rec.start_url}")
                    if wizard:
                        saved = save_wizard(svc.pom, svc.tests, system, rec,
                                            page_names=[str(i.value or "") for i in screen_inputs], **common)
                    else:
                        saved = save_recording(svc.pom, svc.tests, system, rec, page_name=str(page.value or ""),
                                               **common)
                except SAVE_ERRORS as e:
                    ui.notify(f"Not saved: {e}", type="negative", multi_line=True)
                    return
                if wizard:
                    audit("recording.save", f"{system}/{test_id.value}", test_file=saved.test_file,
                          pages=[p[0] for p in saved.pages], test_id=test_id.value)
                    message = (f"{len(saved.pages)} pages saved; test case {test_id.value} added to "
                               f"{saved.test_file}")
                else:
                    audit("recording.save", f"{system}/{saved.page_rel}", test_file=saved.test_file,
                          test_id=test_id.value)
                    verb = "created" if saved.page_created else "updated"
                    message = f"Page {page.value} {verb}; test case {test_id.value} added to {saved.test_file}"
                timer.deactivate()
                dialog.close()
                ui.notify(message, type="positive", multi_line=True)
                gen.tests.reload_list()
                gen.pages.list_view.refresh()
                gen.sections.set_value(gen.tests_tab)
                await gen.tests.open(saved.test_file, saved.test_index)

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Discard", color=None, on_click=lambda: (state.update(recording=None), body.refresh())) \
                    .props("flat unelevated no-caps").classes("any-btn is-38")
                ui.button("Save page and test case", icon=icon("save"), color=None, on_click=save) \
                    .props('data-testid="rec-save"').classes("any-primary")

        body()

        def poll() -> None:
            session = svc.recorder.session
            if state["recording"] is None and session and session.status == "finished" and session.recording:
                state["recording"] = session.recording
                svc.recorder.session = None
                body.refresh()
            elif session and session.status == "failed":
                ui.notify(session.error, type="negative", multi_line=True, timeout=10000)
                svc.recorder.session = None
                body.refresh()

        timer = ui.timer(0.5, poll)
    dialog.open()
