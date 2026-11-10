"""Trends: pass rate by day, the slowest tests, and where failures happen."""
from __future__ import annotations

from nicegui import ui

from webapp.services import trends
from webapp.state import services
from webapp.ui.components import field, icon_button, placeholder, text

RANGES = {7: "Last 7 days", 14: "Last 14 days", 30: "Last 30 days", 90: "Last 90 days"}


def open_trends(system: str | None = None) -> None:
    svc = services()
    runs = svc.runs.list()
    systems = sorted({r.config.system for r in runs})
    if not systems:
        ui.notify("No runs yet: trends need some history.", type="info")
        return
    state = {"system": system if system in systems else systems[0], "env": "", "days": 30}

    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Trends"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "Run history", "any-eyebrow")
                text("h2", "Trends", "any-title")
            icon_button("x", "Close", dialog.close)

        def envs() -> list[str]:
            return sorted({r.config.env for r in runs if r.config.system == state["system"]})

        with ui.element("div").classes("any-form-row"):
            with field("System", "tr-system"):
                system_select = ui.select(systems, value=state["system"]).props('for="tr-system"').classes("w-44")
            with field("Environment", "tr-env"):
                env_select = placeholder(ui.select(envs(), value=None, clearable=True).props('for="tr-env"')
                                         .classes("w-44"), "All")
            with field("Period", "tr-days"):
                days_select = ui.select(RANGES, value=30).props('for="tr-days"').classes("w-44")

        def changed() -> None:
            if system_select.value != state["system"]:
                state["system"] = system_select.value
                env_select.set_options(envs(), value=None)
            state.update(env=env_select.value or "", days=days_select.value)
            body.refresh()

        for el in (system_select, env_select, days_select):
            el.on_value_change(changed)

        @ui.refreshable
        def body() -> None:
            system_, env_, days = state["system"], state["env"], int(state["days"])
            points = trends.daily(runs, days, system_, env_)
            overall = trends.summary(points)
            with ui.element("section").classes("any-trend").props('data-testid="trend-days"'):
                text("div", "Pass rate by day", "any-eyebrow")
                if not overall["results"]:
                    ui.label("No finished runs in this period.").classes("any-muted text-sm")
                else:
                    text("div", f"{overall['rate']}% of {overall['results']} test results passed, "
                                f"{overall['runs']} run{'s' if overall['runs'] != 1 else ''}", "any-trend-sum")
                    biggest = max(p.total for p in points) or 1
                    with ui.element("div").classes("any-bars").props('role="img" aria-label="Pass rate by day"'):
                        for p in points:
                            label = (f"{p.day}: {p.passed} passed, {p.failed} failed ({p.rate}%)" if p.total
                                     else f"{p.day}: no results")
                            with ui.element("div").classes("any-bar").props(f'title="{label}" data-day="{p.day}"'):
                                if p.total:
                                    height = max(6, round(100 * p.total / biggest))
                                    with ui.element("div").classes("any-bar-stack").style(f"height:{height}%"):
                                        if p.failed:
                                            ui.element("i").classes("is-fail").style(
                                                f"flex:{p.failed}")
                                        ui.element("i").classes("is-pass").style(f"flex:{p.passed or 0.0001}")
                    with ui.element("div").classes("any-bar-axis"):
                        text("span", points[0].day)
                        text("span", points[-1].day)

            slow = trends.slowest(runs, 10, system_, env_)
            with ui.element("section").classes("any-trend").props('data-testid="trend-slowest"'):
                text("div", "Slowest tests (average over the last 20 runs)", "any-eyebrow")
                if not slow:
                    ui.label("Nothing to show yet.").classes("any-muted text-sm")
                else:
                    top = slow[0].average or 1
                    for s in slow:
                        with ui.element("div").classes("any-hbar-row"):
                            text("span", s.test_id + (f" ({s.browser})" if s.browser else ""), "any-hbar-name")
                            with ui.element("div").classes("any-hbar"):
                                ui.element("i").style(f"width:{max(2, round(100 * s.average / top))}%")
                            text("span", f"{s.average:g} s avg, {s.slowest:g} s max, last {s.latest:g} s",
                                 "any-hbar-val")

            spots = trends.failure_spots(runs, lambda r: svc.artifacts.details(r.id, r.results_dir), 15,
                                         system_, env_)
            with ui.element("section").classes("any-trend").props('data-testid="trend-spots"'):
                text("div", "Where tests fail (last 15 runs)", "any-eyebrow")
                if not spots:
                    ui.label("No failures in the recent runs.").classes("any-muted text-sm")
                else:
                    with ui.element("table").classes("any-table is-compact w-full"):
                        with ui.element("thead"):
                            with ui.element("tr"):
                                for head in ("Screen > field (action)", "Failures", "Test cases", "Runs", "Latest run"):
                                    text("th", head)
                        with ui.element("tbody"):
                            for sp in spots:
                                with ui.element("tr"):
                                    text("td", sp.where)
                                    text("td", str(sp.failures), "any-num")
                                    text("td", str(len(sp.tests)), "any-num")
                                    text("td", str(len(sp.runs)), "any-num")
                                    text("td", sp.last_run, "any-num")

        body()
    dialog.open()
