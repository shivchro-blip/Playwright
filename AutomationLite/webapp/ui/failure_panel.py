"""What went wrong: the failed tests of a run with where each stopped, a run-level cause said once, and a way into
the details, the test case and the console line. Used by Control and by the Generator's run panel."""
from __future__ import annotations

from typing import Awaitable, Callable

from nicegui import ui

from webapp.services.failure_summary import summarize
from webapp.ui.components import text
from webapp.ui.theme import icon


def failure_panel(run, rows: list[dict], *, open_details: Callable[[dict], None] | None = None,
                  jump: Callable[..., Awaitable[None]] | None = None, show_in_log: Callable[[str], None] | None = None,
                  turn_on_headless: Callable[[], None] | None = None, only_shared: bool = False) -> None:
    """only_shared: just the run-level cause, when the screen already lists the failed tests itself."""
    summary = summarize(rows)
    if summary is None or (only_shared and summary.shared is None):
        return
    by_id = {r["nodeid"]: r for r in rows}
    with ui.element("section").classes("any-fail").props('aria-label="What went wrong" data-testid="what-went-wrong"'):
        text("h3", "What went wrong", "any-eyebrow")
        if summary.shared:
            shared = summary.shared
            with ui.element("div").classes("any-callout is-inline").props('role="status" data-testid="shared-cause"'):
                ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
                with ui.element("div").classes("any-callout-body"):
                    ui.label(shared.headline).classes("any-callout-title")
                    text("div", shared.detail, "any-callout-text")
                    text("div", shared.hint, "any-callout-text")
                    if shared.action == "headless" and turn_on_headless:
                        ui.button("Turn on Headless", icon=icon("check"), color=None, on_click=turn_on_headless) \
                            .props('flat unelevated no-caps data-testid="fix-headless"').classes("any-btn is-32")
        with ui.element("ul").classes("any-fail-list") if not only_shared else ui.element("div"):
            for item in ([] if only_shared else summary.failed):
                with ui.element("li").classes("any-fail-item").props('data-testid="failed-test"'):
                    with ui.element("div").classes("any-fail-what"):
                        text("span", item.name, "any-tc-name")
                        text("span", f"{item.test_id} · {item.browser}", "any-tc-code")
                        if item.where:
                            text("span", item.where, "any-where")
                        text("span", item.message or "No message", "any-msg is-fail")
                    with ui.element("div").classes("any-fail-actions"):
                        row = by_id.get(item.nodeid)
                        if item.has_details and open_details and row:
                            ui.button("Details", icon=icon("file-text"), color=None,
                                      on_click=lambda r=row: open_details(r)) \
                                .props('flat unelevated no-caps dense data-testid="fail-details"').classes("any-btn")
                        if jump:
                            ui.button("Open test case", icon=icon("pencil"), color=None,
                                      on_click=lambda t=item.test_id: jump("test", run.config.system,
                                                                           t.split("[")[0])) \
                                .props('flat unelevated no-caps dense data-testid="fail-open-test"').classes("any-btn")
                        if show_in_log:
                            ui.button("Show in log", icon=icon("terminal"), color=None,
                                      on_click=lambda t=item.test_id: show_in_log(t)) \
                                .props('flat unelevated no-caps dense data-testid="fail-show-log"').classes("any-btn")
        if summary.more:
            text("div", f"... and {summary.more} more failed {'test' if summary.more == 1 else 'tests'}: see the "
                        "results table.", "any-muted text-xs")
