"""The panel shown while a run waits (pause on failure, or stepping through: utils/debug_pause.py): what failed and
where, or which step is next, the page as it was, a way to the test case and the page to fix, a way to fix on the live
page, how long it will wait, what was saved since, and what to do next. Used by Control and by the Generator's run
panel."""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.debug_fix import changed_since
from webapp.services.run_manager import Run, is_truthy
from webapp.state import services
from webapp.ui.components import text
from webapp.ui.paused_fix_dialog import open_fix_dialog
from webapp.ui.theme import icon

Jump = Callable[..., Awaitable[None]]  # jump(kind: "test" | "page", system, name, field="")
WARN_SECONDS = 300  # the last minutes before the pause gives up: say so, and offer to wait longer


async def _confirm(message: str, ok: str) -> bool:
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog"):
        ui.label(message).classes("any-dialog-text")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38")
            ui.button(ok, color=None, on_click=lambda: dialog.submit(True)).classes("any-primary")
    result = await dialog
    dialog.delete()
    return bool(result)


def _clock(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def debug_banner(run: Run, jump: Jump | None = None, has_unsaved: Callable[[], bool] | None = None,
                 on_fixed: Callable[[str, str, str], None] | None = None) -> None:
    """Build the panel for a run that is waiting (nothing when it is not). on_fixed(system, page, test_id) is told when
    the page or the test case was changed from here, so an editor that has it open can reload."""
    paused = run.paused
    if not paused:
        return
    svc = services()
    seq = paused.get("seq")
    field = str(paused.get("field") or "")
    screen = str(paused.get("screen") or "")
    kind = str(paused.get("kind") or "")
    before_step = paused.get("reason") == "step"
    test_id = str(paused.get("test") or (run.config.test_ids[0] if run.config.test_ids else ""))
    system = run.config.system
    headless = is_truthy(run.config.options.get("headlessMode"))
    port = int(paused.get("cdp_port") or 0)
    step_abs = int(paused.get("step_abs") or paused.get("step") or 0)

    async def act(do: str) -> None:
        if not guard("run", "resume a paused run", run.id, system=system, env=run.config.env):
            return
        if do in ("retry", "restep", "skip", "go") and has_unsaved and has_unsaved() and not await _confirm(
                "You have unsaved changes in the Generator. They are not on disk, so the run will not see them. "
                "Resume without them?", "Resume anyway"):
            return
        if not svc.runs.debug_command(run.id, do):
            ui.notify("The run is not waiting any more.", type="warning")
            return
        audit("run.debug", run.id, do=do, test_id=test_id)
        words = {"abort": "Ending the test: the failure stands..." if not before_step else "Ending the test...",
                 "skip": "Skipping and going on...", "restep": "Running the step again from its first field...",
                 "retry": "Running again, in the same browser...", "go": "Running to the end without stopping...",
                 "savestate": "Saving the browser's state...", "extend": "Waiting longer..."}
        ui.notify(words[do], type="info")

    title = ("Paused before step %s: %s" % (step_abs, paused.get("label") or kind)) if before_step else \
        "Paused: signing in failed" if kind == "login" else "Paused: the browser is still open"
    with ui.element("div").classes("any-callout any-debug").props(
            f'role="region" aria-label="Run paused" data-testid="debug-banner" data-seq="{seq}" data-cdp="{port}"'):
        ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
        with ui.element("div").classes("any-callout-body"):
            ui.label(title).classes("any-callout-title")
            where = f"Step {paused.get('step')} of {paused.get('of')}: {paused.get('label') or kind}"
            if kind == "login":
                where = f"Signing in to {system}"
            elif field:
                where += f", field '{field}'" + (f" ({paused.get('action')})" if paused.get("action") else "")
            with ui.element("div").classes("any-callout-text"):
                text("div", f"{test_id} · {where}")
                if before_step:
                    text("div", "Nothing has failed. Look around in the browser, change the test or the page, "
                                "then go on.").props('data-testid="debug-error"')
                else:
                    text("div", str(paused.get("error") or ""), "is-fail").props('data-testid="debug-error"')
            if paused.get("missing_field"):
                text("div", f"The page {screen or 'for this step'} has no field named '{field}'. Add it to the page: "
                            "pick the element in Fix on the live page and use it for this field, or open the page "
                            "and add the field there. Then run again.", "any-callout-text") \
                    .props('data-testid="debug-missing-field"')
            if paused.get("notice"):
                text("div", str(paused["notice"]), "any-callout-text any-err-line").props('data-testid="debug-notice"')
            if paused.get("info"):
                text("div", str(paused["info"]), "any-callout-text").props('data-testid="debug-info"')
            if paused.get("has_picture"):
                with ui.element("div").classes("any-debug-pic"):
                    ui.element("img").props(f'src="/runs/{run.id}/debug/paused.png?v={seq}" '
                                            'alt="The page when the test stopped"')
            text("div", ("Headless is on, so there is no browser window: the picture is the page as it was when the "
                         "test stopped. Turn Headless off to see and click in the browser itself.") if headless else
                 "Look at the browser window, even click around in it. Fix the test case or the page, save it, "
                 "then go on. Nothing is run again that already ran.", "any-callout-text")
            with ui.element("div").classes("any-debug-actions"):
                if jump:
                    if test_id:
                        ui.button("Open test case", icon=icon("pencil"), color=None,
                                  on_click=lambda: jump("test", system, test_id)) \
                            .props('flat unelevated no-caps data-testid="debug-open-test"').classes("any-btn is-32")
                    if screen:
                        ui.button(f"Open page {screen}", icon=icon("app-window"), color=None,
                                  on_click=lambda: jump("page", system, screen, field)) \
                            .props('flat unelevated no-caps data-testid="debug-open-page"').classes("any-btn is-32")
                if port and kind != "login":
                    ui.button("Fix on the live page", icon=icon("scan-search"), color=None,
                              on_click=lambda: open_fix_dialog(run, paused, on_fixed)) \
                        .props('flat unelevated no-caps data-testid="debug-fix"').classes("any-btn is-32") \
                        .tooltip("Check or replace a locator, or add a field, on the page as the test left it")
                if step_abs > 1 and kind != "login":
                    ui.button("Save state here", icon=icon("save"), color=None, on_click=lambda: act("savestate")) \
                        .props('flat unelevated no-caps data-testid="debug-savestate"').classes("any-btn is-32") \
                        .tooltip(f"Keep the browser as it is, so Run from step {step_abs} can start from here later")
            with ui.element("div").classes("any-debug-actions"):
                if before_step:
                    ui.button("Run this step", icon=icon("play-solid"), color=None, on_click=lambda: act("retry")) \
                        .props('unelevated no-caps data-testid="debug-retry"').classes("any-primary")
                    ui.button("Run to the end", color=None, on_click=lambda: act("go")) \
                        .props('flat unelevated no-caps data-testid="debug-go"').classes("any-btn is-38") \
                        .tooltip("Run this step and every one after it without stopping again")
                    ui.button("Skip this step", color=None, on_click=lambda: act("skip")) \
                        .props('flat unelevated no-caps data-testid="debug-skip"').classes("any-btn is-38")
                else:
                    label = "Try signing in again" if kind == "login" else \
                        f"Run '{field}' again" if field else "Run again"
                    ui.button(label, icon=icon("rotate-cw"), color=None, on_click=lambda: act("retry")) \
                        .props('unelevated no-caps data-testid="debug-retry"').classes("any-primary")
                    if field and not paused.get("missing_field"):
                        ui.button("Run the step again", color=None, on_click=lambda: act("restep")) \
                            .props('flat unelevated no-caps data-testid="debug-restep"').classes("any-btn is-38") \
                            .tooltip("From the step's first field. Fields that clicked or submitted something "
                                     "run twice.")
                        if paused.get("can_skip"):
                            ui.button("Skip this field", color=None, on_click=lambda: act("skip")) \
                                .props('flat unelevated no-caps data-testid="debug-skip"').classes("any-btn is-38") \
                                .tooltip("Leave this field out and carry on with the next")
                ui.button("End the test", icon=icon("square-solid"), color=None, on_click=lambda: act("abort")) \
                    .props('flat unelevated no-caps data-testid="debug-abort"').classes("any-btn is-38 is-danger") \
                    .tooltip("The test fails here and the browser closes")
            with ui.element("div").classes("any-debug-actions"):
                countdown = text("span", "", "any-muted text-xs").props('data-testid="debug-countdown" role="timer"')
                keep = ui.button("Keep waiting", color=None, on_click=lambda: act("extend")) \
                    .props('flat unelevated no-caps dense data-testid="debug-extend"').classes("any-btn")
            files = text("div", "", "any-muted text-xs").props('data-testid="debug-changed"')

            def refresh_clock() -> None:
                current = run.paused or {}
                left = float(current.get("deadline_at") or 0) - time.time()
                countdown.text = f"Waits {_clock(left)} more, then the test ends. Other runs queue behind this one."
                keep.set_visibility(left < WARN_SECONDS)
                changed = changed_since(svc.settings.workspace, system, float(current.get("since") or time.time()))
                files.text = ("Saved since the pause (the run will read these again): " + ", ".join(
                    f"{path} ({time.strftime('%H:%M:%S', time.localtime(when))})" for path, when in changed)
                    if changed else "Nothing saved since the pause yet: save your fix before you go on.")

            refresh_clock()
            ui.timer(1.0, refresh_clock)


def debug_note(run: Run) -> None:
    """A finished run that paused: it is not a clean result, and says so."""
    if not run.debug_pauses or run.active:
        return
    with ui.element("div").classes("any-callout is-inline").props('role="status" data-testid="debug-note"'):
        ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
        text("div", f"This run paused {run.debug_pauses} time{'' if run.debug_pauses == 1 else 's'} for debugging, "
                    "so it is not a clean result: it is left out of trends, flaky detection and comparisons with "
                    "the last run.", "any-callout-text")
