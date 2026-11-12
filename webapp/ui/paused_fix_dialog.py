"""Fix on the live page: work on the page of a paused test without leaving the browser it is in.

The paused browser (Chromium, launched with a remote debugging port: utils/debug_pause.py) is looked at in place: its
picture is shown, a click on it picks the element under the click and finds the most reliable locator for it (as the
Studio does), the field's current locator can be checked against the page as it is now, and replacements for it can
be ranked. Accepting one writes the page file; adding a field writes the test case; nothing moves the page, and the
run is only resumed by an explicit button."""
from __future__ import annotations

import base64
from typing import Callable

from nicegui import background_tasks, ui
from nicegui import run as nrun

from webapp.auth import audit, guard
from webapp.services import debug_fix, paused_page
from webapp.services.json_files import ConflictError, PathError
from webapp.services.studio import Picked, StudioSession
from webapp.state import services
from webapp.ui.components import _describe, text
from webapp.ui.theme import icon

ACTION_WORDS = {"fill": "Type", "click": "Click", "select": "Choose", "check": "Tick", "press": "Press a key"}
ACTION_FOR = {"text": "fill", "textarea": "fill", "number": "fill", "select": "select", "checkbox": "check",
              "radio": "check"}
SAVE_ERRORS = (debug_fix.DebugFixError, ConflictError, PathError, ValueError, OSError, KeyError, IndexError)


async def open_fix_dialog(run, paused: dict, on_fixed: Callable[[str, str, str], None] | None = None) -> None:
    svc = services()
    port = int(paused.get("cdp_port") or 0)
    if not port:
        ui.notify("This browser cannot be looked at from here (only Chromium browsers can: chrome, msedge, "
                  "chromium).", type="warning")
        return
    system, screen = run.config.system, str(paused.get("screen") or "")
    field = str(paused.get("field") or "")
    test_id = str(paused.get("test") or "")
    step_abs = int(paused.get("step_abs") or paused.get("step") or 0)
    before_step = paused.get("reason") == "step"
    position = 0 if before_step else max(int(paused.get("action_no") or 1) - 1, 0)
    url = str(paused.get("url") or "")
    session = StudioSession()
    try:
        await session.attach(paused_page.endpoint(port), url)
    except Exception as e:  # noqa: BLE001 - the browser ended, or is not Chromium: say so
        ui.notify(f"Could not look at the paused browser: {str(e).splitlines()[0]}", type="negative")
        return
    state: dict = {"picked": None, "busy": False}

    def field_cfg() -> dict | None:
        try:
            return svc.pom.field_configs(system, screen).get(field) if screen and field else None
        except (ValueError, OSError):
            return None

    def taken() -> set[str]:
        try:
            return set(svc.pom.field_names(system, screen)) if screen else set()
        except (ValueError, OSError):
            return set()

    def fixed(message: str) -> None:
        audit("run.debug.fix", run.id, test_id=test_id, page=screen, field=field)
        ui.notify(message, type="positive", timeout=7000)
        if on_fixed:
            on_fixed(system, screen, test_id)

    with ui.dialog().props("maximized=false") as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Fix on the live page"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Paused test", "any-eyebrow")
                text("h2", "Fix on the live page", "any-title any-details-name")
                text("div", f"{test_id} · {screen}" + (f" · field '{field}'" if field else ""), "any-tc-code")
            ui.button(icon=icon("x"), color=None, on_click=dialog.close) \
                .props('flat unelevated aria-label="Close" data-testid="fix-close"').classes("any-iconbtn")
        text("div", "The browser stays where it is. Click the element the test should use, check the field's current "
                    "locator, or ask for replacements. Nothing runs again until you resume.", "any-muted text-sm")
        with ui.element("div").classes("any-debug-actions"):
            ui.button("Refresh the picture", icon=icon("rotate-cw"), color=None, on_click=lambda: refresh()) \
                .props('flat unelevated no-caps data-testid="fix-refresh"').classes("any-btn is-38")
            if field and screen:
                ui.button(f"Check '{field}'", icon=icon("scan-search"), color=None, on_click=lambda: do_check()) \
                    .props('flat unelevated no-caps data-testid="fix-check"').classes("any-btn is-38")
                ui.button("Suggest fixes", icon=icon("sparkles"), color=None, on_click=lambda: do_suggest()) \
                    .props('flat unelevated no-caps data-testid="fix-suggest"').classes("any-btn is-38")
        picture = ui.interactive_image(on_mouse=lambda e: on_click(e), events=["click"], cross=True) \
            .props('data-testid="fix-picture"').classes("any-studio-picture")
        results = ui.element("div").classes("flex flex-col gap-3 w-full").props('data-testid="fix-results"')

        async def refresh() -> None:
            try:
                picture.set_source(await session.screenshot())
            except Exception as e:  # noqa: BLE001
                ui.notify(f"Could not take a picture: {str(e).splitlines()[0]}", type="negative")

        def show_check(res) -> None:
            with results:
                results.clear()
                if res.error:
                    text("pre", res.error, "any-pre is-error").props('data-testid="fix-check-result"')
                    return
                tone = "passed" if res.count == 1 else "failed" if res.count == 0 else "idle"
                words = "No matches" if res.count == 0 else f"{res.count} match" + ("" if res.count == 1 else "es")
                with ui.element("div").classes("any-lc-summary").props('data-testid="fix-check-result"'):
                    with ui.element("span").classes(f"any-pill pill-{tone}"):
                        ui.element("span").classes("dot").props('aria-hidden="true"')
                        text("span", words)
                    text("code", res.description, "any-lc-desc")
                for warning in res.warnings:
                    text("div", warning, "any-callout-text")
                if res.screenshot:
                    src = "data:image/png;base64," + base64.b64encode(res.screenshot).decode()
                    with ui.element("figure").classes("any-shot"):
                        ui.element("img").props(f'src="{src}" alt="The page with the matches outlined"')
                        text("figcaption", "Matches outlined in blue; the one index picks in red.")

        async def do_check() -> None:
            cfg = field_cfg()
            if not cfg:
                ui.notify(f"Page {screen} has no field '{field}' to check.", type="warning")
                return
            try:
                show_check(await nrun.io_bound(paused_page.check, port, url, dict(cfg)))
            except Exception as e:  # noqa: BLE001
                with results:
                    results.clear()
                    text("pre", str(e).splitlines()[0], "any-pre is-error").props('data-testid="fix-check-result"')

        def use(cfg: dict, field_type: str = "") -> None:
            if not guard("edit", "fix a page from a paused run", f"{system}/{screen}", system=system):
                return
            try:
                debug_fix.apply_locator(svc.pom, system, screen, field, cfg, field_type)
            except SAVE_ERRORS as e:
                ui.notify(f"Not saved: {e}", type="negative", multi_line=True)
                return
            fixed(f"Saved the new locator for '{field}' in {screen}. Run it again from the paused panel.")

        async def do_suggest() -> None:
            cfg = field_cfg() or {}
            try:
                fixes = await nrun.io_bound(paused_page.suggest, port, url, field, cfg, taken())
            except Exception as e:  # noqa: BLE001
                ui.notify(str(e).splitlines()[0], type="negative")
                return
            with results:
                results.clear()
                if not fixes.ok:
                    text("pre", fixes.error, "any-pre is-error")
                    return
                if not fixes.suggestions:
                    text("div", f"Nothing on the page looks like '{field}': click the element in the picture.",
                         "any-muted text-sm").props('data-testid="fix-no-suggestions"')
                    return
                text("div", f"Elements on the page that could replace '{field}':", "any-eyebrow")
                for s in fixes.suggestions:
                    with ui.element("div").classes("any-fix-row").props('data-testid="fix-suggestion"'):
                        text("span", f"{s.label or s.type} · {s.score}%", "any-tc-name")
                        text("code", _describe(s.cfg), "any-lc-desc")
                        text("span", s.reason, "any-muted text-xs")
                        ui.button("Use", color=None, on_click=lambda c=s.cfg, t=s.type: use(c, t)) \
                            .props('flat unelevated no-caps dense data-testid="fix-use"').classes("any-btn")

        def show_picked(p: Picked) -> None:
            state["picked"] = p
            with results:
                results.clear()
                with ui.element("div").classes("any-studio-picked").props('data-testid="fix-picked"'):
                    text("div", f"{p.tag} · {p.label}" if p.label else p.tag, "any-muted text-sm")
                    text("div", _describe(p.cfg) + (f" ({p.matches} match, index {p.cfg.get('index')})"
                                                     if p.matches > 1 else ""), "mono text-sm")
                if field:
                    ui.button(f"Use this for '{field}'", icon=icon("check"), color=None,
                              on_click=lambda: use(p.cfg, p.type)) \
                        .props('unelevated no-caps data-testid="fix-use-picked"').classes("any-primary")
                if test_id and step_abs and kind_is_page():
                    text("div", "Or add it to the test as a new field" +
                         ("" if before_step else f" before '{field}'" if field else "") + ":", "any-eyebrow")
                    name = ui.input("Field name", value=p.name).props('data-testid="fix-new-name"') \
                        .classes("w-full mono-input")
                    with ui.element("div").classes("any-form-row"):
                        action = ui.select({a: ACTION_WORDS[a] for a in debug_fix.ACTIONS},
                                           value=ACTION_FOR.get(p.type, "click"), label="Do") \
                            .props('data-testid="fix-new-action"').classes("w-40")
                        value = ui.input("Value").props('data-testid="fix-new-value"').classes("flex-1")
                    value.bind_visibility_from(action, "value", backward=lambda a: a in ("fill", "select", "press"))
                    ui.button("Add it and run again", icon=icon("play-solid"), color=None,
                              on_click=lambda: add_and_run(p, str(name.value or "").strip(), str(action.value),
                                                           str(value.value or ""))) \
                        .props('unelevated no-caps data-testid="fix-add"').classes("any-primary")

        def kind_is_page() -> bool:
            return paused.get("kind") in ("page", "common") or bool(screen)

        def add_and_run(p: Picked, name: str, action: str, value: str) -> None:
            if not name:
                ui.notify("Give the field a name.", type="warning")
                return
            if not guard("edit", "add a field from a paused run", f"{system}/{screen}", system=system) or \
                    not guard("run", "resume a paused run", run.id, system=system, env=run.config.env):
                return
            try:
                if name in taken() and svc.pom.field_configs(system, screen).get(name, {}).get("locator") \
                        != p.cfg.get("locator"):
                    ui.notify(f"'{name}' is already another field on {screen}: choose another name.",
                              type="warning")
                    return
                debug_fix.apply_locator(svc.pom, system, screen, name, p.cfg, p.type)
                debug_fix.add_field_to_test(svc.tests, system, test_id, step_abs, name, action, value,
                                            position)
            except SAVE_ERRORS as e:
                ui.notify(f"Not saved: {e}", type="negative", multi_line=True)
                return
            audit("run.debug.add-field", run.id, test_id=test_id, page=screen, field=name)
            if on_fixed:
                on_fixed(system, screen, test_id)
            if services().runs.debug_command(run.id, "retry"):
                ui.notify(f"Added '{name}' to the test and running again.", type="positive")
            else:
                ui.notify("Added to the test, but the run is not waiting any more.", type="warning")
            dialog.close()

        async def on_click(e) -> None:
            if state["busy"]:
                return
            state["busy"] = True
            try:
                picked = await session.pick(float(e.image_x), float(e.image_y), taken())
                if picked is None:
                    ui.notify("Nothing to use there: click a box, a button or a choice.", type="info")
                else:
                    show_picked(picked)
            except Exception as err:  # noqa: BLE001
                ui.notify(str(err).splitlines()[0], type="negative")
            finally:
                state["busy"] = False

        dialog.on("hide", lambda: background_tasks.create(session.close(), name="fix-detach"))
    dialog.open()
    await refresh()
