"""The panel shown while a run waits after a failure (pause on failure, utils/debug_pause.py): what failed and where,
the page as it was, a way to the test case and the page to fix, and what to do next. Used by Control and by the
Generator's run panel."""
from __future__ import annotations

from typing import Awaitable, Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.run_manager import Run, is_truthy
from webapp.state import services
from webapp.ui.components import text
from webapp.ui.theme import icon

Jump = Callable[..., Awaitable[None]]  # jump(kind: "test" | "page", system, name, field="")


async def _confirm(message: str, ok: str) -> bool:
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog"):
        ui.label(message).classes("any-dialog-text")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38")
            ui.button(ok, color=None, on_click=lambda: dialog.submit(True)).classes("any-primary")
    result = await dialog
    dialog.delete()
    return bool(result)


def debug_banner(run: Run, jump: Jump | None = None, has_unsaved: Callable[[], bool] | None = None) -> None:
    """Build the panel for a run that is waiting (nothing when it is not)."""
    paused = run.paused
    if not paused:
        return
    seq = paused.get("seq")
    field = str(paused.get("field") or "")
    screen = str(paused.get("screen") or "")
    test_id = str(paused.get("test") or (run.config.test_ids[0] if run.config.test_ids else ""))
    system = run.config.system
    headless = is_truthy(run.config.options.get("headlessMode"))

    async def act(do: str) -> None:
        if not guard("run", "resume a paused run", run.id, system=system, env=run.config.env):
            return
        if do != "abort" and has_unsaved and has_unsaved() and not await _confirm(
                "You have unsaved changes in the Generator. They are not on disk, so the run will not see them. "
                "Resume without them?", "Resume anyway"):
            return
        if not services().runs.debug_command(run.id, do):
            ui.notify("The run is not waiting any more.", type="warning")
            return
        audit("run.debug", run.id, do=do, test_id=test_id)
        ui.notify({"abort": "Ending the test: the failure stands...", "skip": "Skipping the field and going on...",
                   "restep": "Running the step again from its first field...",
                   "retry": "Running the field again, in the same browser..."}[do], type="info")

    with ui.element("div").classes("any-callout any-debug").props(
            f'role="region" aria-label="Run paused after a failure" data-testid="debug-banner" '
            f'data-seq="{seq}"'):
        ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
        with ui.element("div").classes("any-callout-body"):
            ui.label("Paused: the browser is still open").classes("any-callout-title")
            where = f"Step {paused.get('step')} of {paused.get('of')}: {paused.get('label') or paused.get('kind')}"
            if field:
                where += f", field '{field}'" + (f" ({paused.get('action')})" if paused.get("action") else "")
            with ui.element("div").classes("any-callout-text"):
                text("div", f"{test_id} · {where}")
                text("div", str(paused.get("error") or ""), "is-fail").props('data-testid="debug-error"')
            if paused.get("notice"):
                text("div", str(paused["notice"]), "any-callout-text any-err-line").props('data-testid="debug-notice"')
            if paused.get("has_picture"):
                with ui.element("div").classes("any-debug-pic"):
                    ui.element("img").props(f'src="/runs/{run.id}/debug/paused.png?v={seq}" '
                                            'alt="The page when the step failed"')
            text("div", ("Headless is on, so there is no browser window: the picture is the page as it was when the "
                         "step failed. Turn Headless off to see and click in the browser itself.") if headless else
                 "Look at the browser window, even click around in it. Fix the test case or the page, save it, "
                 "then resume. Nothing is run again that already ran.", "any-callout-text")
            if jump:
                with ui.element("div").classes("any-debug-actions"):
                    if test_id:
                        ui.button("Open test case", icon=icon("pencil"), color=None,
                                  on_click=lambda: jump("test", system, test_id)) \
                            .props('flat unelevated no-caps data-testid="debug-open-test"').classes("any-btn is-32")
                    if screen:
                        ui.button(f"Open page {screen}", icon=icon("app-window"), color=None,
                                  on_click=lambda: jump("page", system, screen, field)) \
                            .props('flat unelevated no-caps data-testid="debug-open-page"').classes("any-btn is-32")
            with ui.element("div").classes("any-debug-actions"):
                label = f"Run '{field}' again" if field else "Run again"
                ui.button(label, icon=icon("rotate-cw"), color=None, on_click=lambda: act("retry")) \
                    .props('unelevated no-caps data-testid="debug-retry"').classes("any-primary")
                if field:
                    ui.button("Run the step again", color=None, on_click=lambda: act("restep")) \
                        .props('flat unelevated no-caps data-testid="debug-restep"').classes("any-btn is-38") \
                        .tooltip("From the step's first field. Fields that clicked or submitted something run twice.")
                    ui.button("Skip this field", color=None, on_click=lambda: act("skip")) \
                        .props('flat unelevated no-caps data-testid="debug-skip"').classes("any-btn is-38") \
                        .tooltip("Leave this field out and carry on with the next")
                ui.button("End the test", icon=icon("square-solid"), color=None, on_click=lambda: act("abort")) \
                    .props('flat unelevated no-caps data-testid="debug-abort"').classes("any-btn is-38 is-danger") \
                    .tooltip("The failure stands: the test fails and the browser closes")
            text("div", f"Waits up to {paused.get('timeout_minutes', 30):g} minutes. Other runs queue behind this one.",
                 "any-muted text-xs")
