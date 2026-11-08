"""Control > Plan: the test cases worth running in a time budget (webapp/services/planner.py), why each one is in or
left out, and a run of just those."""
from __future__ import annotations

from typing import Callable

from nicegui import run as nrun
from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.planner import plan_for
from webapp.state import services
from webapp.ui.components import text
from webapp.ui.theme import icon


def open_plan(system: str | None, env: str | None, on_started: Callable[[str], None]) -> None:
    svc = services()
    if not system or not env:
        ui.notify("Pick a system and an environment first.", type="warning")
        return
    state: dict = {"plan": None}
    with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[52rem]").props(
            'role="dialog" aria-label="Plan a run" data-testid="plan-dialog"'):
        text("h2", f"Run what matters: {system} / {env}", "any-title")
        ui.label("Picks the test cases worth running in the time you have: what a change reaches, critical "
                 "journeys, recent failures, flaky and stale ones. Critical journeys are always in.") \
            .classes("any-muted text-sm")
        with ui.element("div").classes("any-form-row"):
            budget = ui.number("Time budget (minutes)", value=10, min=1, max=600, step=1) \
                .props('data-testid="plan-budget"').classes("w-48")
            base = ui.input("Change since (git commit or branch, optional)") \
                .props('data-testid="plan-base"').classes("flex-1")
            ui.button("Plan", icon=icon("list-checks"), color=None, on_click=lambda: make()) \
                .props('data-testid="plan-make"').classes("any-btn is-38")
        body = ui.element("div").classes("flex flex-col gap-2 w-full")

        async def make() -> None:
            body.clear()
            try:
                result = await nrun.io_bound(plan_for, svc, system, env, float(budget.value or 10) * 60,
                                             base=(base.value or "").strip())
            except Exception as e:  # noqa: BLE001 - a bad git ref, an unreadable workspace: say it
                with body:
                    ui.label(str(e).splitlines()[0] if str(e) else "Could not plan.").classes("any-err-line")
                run_button.set_enabled(False)
                return
            state["plan"] = result
            with body:
                n = len(result.chosen)
                text("div", f"{n} test case{'' if n == 1 else 's'}, about {result.seconds / 60:.1f} of "
                            f"{result.budget_seconds / 60:.0f} minutes", "any-title is-sm") \
                    .props('data-testid="plan-summary"')
                for note in result.notes:
                    text("div", note, "any-warn-line text-sm")
                with ui.element("ul").classes("any-auto-recent").props('data-testid="plan-chosen"'):
                    for p in result.chosen:
                        with ui.element("li"):
                            text("span", p.test_id, "any-auto-name mono")
                            text("span", f"{p.seconds:.0f} s", "any-muted text-xs")
                            for reason in p.reasons:
                                text("span", reason, "any-tag" + (" is-failed" if "critical" in reason
                                                                  or "breaks" in reason else ""))
                if result.left_out:
                    with ui.expansion(f"Left out ({len(result.left_out)})", icon=icon("circle-help")) \
                            .classes("any-disclosure").props('data-testid="plan-left-out"'):
                        with ui.element("ul").classes("any-auto-recent"):
                            for p in result.left_out:
                                with ui.element("li"):
                                    text("span", p.test_id, "mono")
                                    text("span", "; ".join(p.reasons), "any-muted text-sm")
            run_button.set_enabled(bool(result.chosen))
            run_button.text = f"Run these {n}"

        def start() -> None:
            result = state["plan"]
            if result is None or not result.chosen or not guard("run", "run a plan", system, system=system, env=env):
                return
            try:
                started = svc.start_tests(system, result.test_ids, env, trigger="plan")
            except Exception as e:  # noqa: BLE001 - the journeys' JourneyError, a full queue
                ui.notify(str(e), type="negative")
                return
            audit("plan.run", started.id, tests=len(result.test_ids), budget=int(result.budget_seconds))
            dialog.close()
            ui.notify(f"Run {started.id} started with {len(result.test_ids)} test cases", type="positive")
            on_started(started.id)

        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Close", color=None, on_click=dialog.close).props("flat no-caps").classes("any-btn")
            run_button = ui.button("Run", icon=icon("play-solid"), color=None, on_click=start) \
                .props('data-testid="plan-run"').classes("any-primary")
            run_button.set_enabled(False)
    dialog.on("hide", dialog.delete)
    dialog.open()
