"""The run strip in the top bar: the run in progress (or how the last one ended) on every tab, one click from Control.

It shows the newest run that is running or waiting; when that run ends it stays, with its result, until it is opened or
dismissed, so a failure is never missed because you were on another tab. It is hidden on Control itself, which shows
the run in full. Updates when a run changes (the event bus), not on a timer."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.state import services
from webapp.ui.components import text
from webapp.ui.live import live
from webapp.ui.theme import icon


def describe(run) -> tuple[str, str, str]:
    """(tone, icon, words) for a run: 'Running 2 of 4 · SMOKE/dev', 'Paused: needs you', 'Failed: 2 of 4 tests'."""
    counts = run.counts()
    total = max(run.expected, len(run.results))
    done = sum(v for k, v in counts.items() if k != "running")
    target = f"{run.config.system}/{run.config.env}"
    if run.paused:
        return "warn", "triangle-alert", f"Paused: a test needs you · {target}"
    if run.status == "queued":
        return "run", "loader-circle", f"Queued · {target}"
    if run.status == "running":
        return "run", "loader-circle", f"Running {done} of {total or '?'} · {target}"
    failed = counts.get("failed", 0) + counts.get("error", 0)
    if run.status == "passed":
        return "pass", "circle-check", f"Passed: {done} {'test' if done == 1 else 'tests'} · {target}"
    if run.status == "cancelled":
        return "idle", "square-solid", f"Cancelled · {target}"
    return "fail", "circle-x", f"{'Failed' if run.status == 'failed' else 'Error'}: {failed} of {total or '?'} " \
                               f"{'test' if total == 1 else 'tests'} · {target}"


class RunStrip:
    def __init__(self, open_run: Callable[[str], None], control_shown: Callable[[], bool]):
        """open_run(run_id): show the run on Control. control_shown(): is Control the tab on show."""
        self.svc = services()
        self.open_run, self.control_shown = open_run, control_shown
        self.watching: str | None = None  # the run seen in progress: kept after it ends, until dismissed
        with ui.element("div").classes("any-runstrip").props('role="status" aria-live="polite" '
                                                              'data-testid="run-strip"') as self.root:
            self.view()
        live(self.svc.bus, ["run"], self.refresh, fallback=5.0, always=True)

    def current(self):
        runs = self.svc.runs.list()
        active = [r for r in runs if r.active]
        if active:
            run = next((r for r in active if r.status == "running"), active[0])
            self.watching = run.id
            return run
        return self.svc.runs.get(self.watching) if self.watching else None

    def dismiss(self) -> None:
        self.watching = None
        self.refresh()

    def open(self, run_id: str) -> None:
        self.watching = None
        self.open_run(run_id)
        self.refresh()

    def refresh(self) -> None:
        self.view.refresh()

    @ui.refreshable_method
    def view(self) -> None:
        run = self.current()
        self.root.set_visibility(bool(run) and not self.control_shown())
        if not run:
            return
        tone, ico, words = describe(run)
        queued = sum(1 for r in self.svc.runs.list() if r.status == "queued" and r.id != run.id)
        with ui.button(color=None, on_click=lambda: self.open(run.id)) \
                .props('flat unelevated no-caps dense aria-label="Show this run in Control"') \
                .classes(f"any-runstrip-btn tone-{tone}"):
            ui.icon(icon(ico)).props('aria-hidden="true"')
            ui.label(words)
            if queued:
                text("span", f"+{queued} waiting", "any-runstrip-more")
        if not run.active:
            ui.button(icon=icon("x"), color=None, on_click=self.dismiss) \
                .props('flat unelevated dense aria-label="Dismiss"').classes("any-runstrip-x")
