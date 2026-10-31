"""Generator: add fields to a page by scanning the live page for what a person can interact with."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import run, ui

from webapp.auth import audit, guard
from webapp.services.locator_check import page_url
from webapp.services.page_scan import Candidate, PageScan, scan_page
from webapp.state import services
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.recorder_dialog import _describe
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import PageEditor

SIGNATURE_KEYS = ("locator", "role", "name", "label", "placeholder", "text", "contains", "index")
_REMEMBERED = {"id_attr": "", "id_prefix": ""}  # the site's id attribute, kept for the next scan in this process


def _signature(cfg: dict) -> str:
    return json.dumps({k: cfg.get(k) for k in SIGNATURE_KEYS if cfg.get(k) not in (None, "")}, sort_keys=True)


def open_page_scan(editor: PageEditor) -> None:
    from webapp.ui.generator import FieldRow  # the editor module imports this one

    svc = services()
    gen = editor.gen
    system = gen.system
    if not system:
        ui.notify("Pick a system first", type="warning")
        return
    envs = svc.systems.environments(system)
    env_default = "dev" if "dev" in envs else (envs[0] if envs else None)
    session_file = svc.settings.workspace / "Data" / "session.json"
    present = {_signature(r.cfg) for r in editor.rows}
    state: dict = {"scan": None, "busy": False, "boxes": []}

    def url_for(env: str | None) -> str:
        login = svc.systems.login(system, env) if env else None
        return page_url(login.base_url if login else "", editor.page)

    with ui.dialog().props("persistent") as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Add fields from the live page"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Scan", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", "Add fields from the live page", "any-title any-details-name")
                    text("span", editor._page_name(), "any-tc-code")
            icon_button("x", "Close", dialog.close)

        ui.label("Opens the page in headless Chromium on this machine, lists the buttons, inputs, links, checkboxes "
                 "and menus a person can use, and proposes a name and locator for each.").classes("any-muted text-sm")
        with ui.element("div").classes("any-form-row"):
            if envs:
                with labelled("Environment", "ps-env"):
                    ui.select(envs, value=env_default,
                              on_change=lambda e: setattr(url_input, "value", url_for(e.value))) \
                        .props('for="ps-env"').classes("w-40")
            with labelled("Page URL", "ps-url").classes("is-grow"):
                url_input = ui.input(value=url_for(env_default)).props('for="ps-url" data-testid="scan-url"') \
                    .classes("w-full mono-input")
        with ui.element("div").classes("any-form-row"):
            with labelled("ID attribute (optional)", "ps-attr").classes("is-grow"):
                id_attr = ui.input(value=_REMEMBERED["id_attr"], placeholder="data-attribute-id") \
                    .props('for="ps-attr" data-testid="scan-id-attr"').classes("w-full mono-input")
            with labelled("Remove this prefix from names", "ps-prefix").classes("is-grow"):
                id_prefix = ui.input(value=_REMEMBERED["id_prefix"], placeholder="LSI_APP_") \
                    .props('for="ps-prefix" data-testid="scan-id-prefix"').classes("w-full mono-input")
        has_session = session_file.is_file()
        use_session = ui.switch("Use the saved login session (Data/session.json)", value=True) if has_session else None
        with ui.element("div").classes("any-dialog-actions is-start"):
            scan_button = ui.button("Scan page", icon=icon("scan-search"), color=None) \
                .props('data-testid="scan-start"').classes("any-primary")
            ui.label("Elements inside iframes and custom components without a role are not listed.") \
                .classes("any-muted text-xs")
        result = ui.element("div").classes("flex flex-col gap-2 w-full")

        def render(scan: PageScan) -> None:
            result.clear()
            state["boxes"] = []
            with result:
                if not scan.ok:
                    with ui.element("div").classes("any-error-box"):
                        text("div", "Could not scan", "any-eyebrow")
                        text("pre", scan.error, "any-pre is-error")
                    return
                head = f"{len(scan.candidates)} elements found" + (f", {scan.skipped} skipped (no reliable locator)"
                                                                  if scan.skipped else "")
                text("div", head, "any-eyebrow")
                if scan.title or scan.final_url:
                    text("div", f"{scan.title} - {scan.final_url}", "any-muted text-sm")
                if not scan.candidates:
                    ui.label("Nothing to interact with was found on this page.").classes("any-muted text-sm")
                    return
                with ui.element("div").classes("any-scan-list").props('data-testid="scan-results"'):
                    for c in scan.candidates:
                        here = _signature(c.cfg) in present
                        with ui.element("div").classes("any-scan-row"):
                            box = ui.checkbox(value=not here and not c.disabled) \
                                .props(f'aria-label="Add {c.name}"')
                            box.set_enabled(not here)
                            name = ui.input(value=c.name).props('aria-label="Field name" dense') \
                                .classes("w-52 mono-input")
                            text("span", c.type, "any-tag")
                            with ui.element("div").classes("min-w-0 flex-1"):
                                text("div", _describe(c.cfg), "any-small mono truncate")
                                note = ("already on the page" if here else
                                        f"{c.matches} matches, index {c.cfg.get('index')} picks this one"
                                        if c.matches > 1 else "")
                                text("div", " - ".join(x for x in (c.label, note) if x), "any-muted text-xs truncate")
                        state["boxes"].append((box, name, c))
                with ui.element("div").classes("any-dialog-actions"):
                    ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
                    ui.button("Add selected fields", icon=icon("plus"), color=None, on_click=add) \
                        .props('data-testid="scan-add"').classes("any-primary")

        def add() -> None:
            taken = {r.name for r in editor.rows}
            added: list[str] = []
            for box, name, c in state["boxes"]:
                if not box.value or not box.enabled:
                    continue
                base = (name.value or c.name).strip() or c.name
                final, n = base, 2
                while final in taken:
                    final, n = f"{base}{n}", n + 1
                taken.add(final)
                editor.rows.append(FieldRow(final, {**c.cfg, "type": c.type}))
                added.append(final)
            if not added:
                ui.notify("Tick the fields to add.", type="warning")
                return
            editor.field_filter = ""
            editor.touch()
            editor.fields_view.refresh()
            audit("page.scan", f"{system}/{editor.rel}", added=len(added))
            dialog.close()
            ui.notify(f"Added {len(added)} field(s); review them and Save the page.", type="positive")

        async def do_scan() -> None:
            if state["busy"] or not guard("check", "scan page"):
                return
            state["busy"] = True
            scan_button.props("loading")
            _REMEMBERED.update(id_attr=(id_attr.value or "").strip(), id_prefix=(id_prefix.value or "").strip())
            try:
                storage = session_file if use_session is not None and use_session.value else None
                scan = await run.io_bound(
                    scan_page, (url_input.value or "").strip(), id_attribute=_REMEMBERED["id_attr"],
                    id_prefix=_REMEMBERED["id_prefix"], storage_state=storage)
                state["scan"] = scan
                render(scan)
            except Exception as e:  # e.g. Playwright's browser isn't installed
                render(PageScan(url=url_input.value or "", error=f"{type(e).__name__}: {e}"))
            finally:
                state["busy"] = False
                scan_button.props(remove="loading")

        scan_button.on_click(do_scan)
    dialog.open()


__all__ = ["open_page_scan", "Candidate"]
