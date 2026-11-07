"""A test case's timeline (webapp/services/timeline.py): how it is doing on top, then everything that happened to
it, newest first, with Quarantine/Release and Open in the editor at hand."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.state import services
from webapp.ui.components import icon_button, text
from webapp.ui.theme import icon

STATE_WORDS = {"flaky": ("Flaky", "flaky"), "failing": ("Failing", "failed"), "broke": ("Just broke", "failed"),
               "fixed": ("Just fixed", "passed"), "passing": ("Passing", "passed"), "none": ("No results", "idle")}
KIND_ICONS = {"result": "circle-check", "heal": "sparkles", "fallback": "shield", "drift": "shield",
              "missing": "triangle-alert", "quarantine": "triangle-alert", "release": "check", "edit": "pencil"}
TONE_PILL = {"passed": "pill-passed", "failed": "pill-failed", "warn": "pill-flaky", "info": "pill-idle"}


def _seconds(value: float | None) -> str:
    return "" if value is None else (f"{value:.1f} s" if value < 60 else f"{int(value // 60)} min {int(value % 60)} s")


def open_timeline(test_id: str, system: str, env: str | None = None,
                  on_open: Callable[[str, str], object] | None = None,
                  on_change: Callable[[], object] | None = None) -> None:
    from webapp.api import timeline_for
    from webapp.ui.quarantine_dialog import toggle_quarantine

    svc = services()
    envs = svc.systems.environments(system)
    state = {"env": env if env in envs else None}

    # made at the page's top level: the button that opened it may be re-rendered (on_change refreshes its editor)
    with ui.context.client.layout, ui.dialog() as dialog, \
            ui.card().classes("any-app any-dialog any-details any-timeline").props(
            'role="dialog" aria-label="Test timeline" data-testid="timeline"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", f"Timeline · {system}", "any-eyebrow")
                text("h2", test_id, "any-title mono")
            with ui.element("div").classes("flex items-center gap-2"):
                ui.select({None: "All environments", **{e: e for e in envs}}, value=state["env"],
                          on_change=lambda e: (state.update(env=e.value), body.refresh())) \
                    .props('dense aria-label="Environment" data-testid="timeline-env"').classes("w-44")
                icon_button("x", "Close", dialog.close)

        @ui.refreshable
        def body() -> None:
            tl = timeline_for(test_id, system, state["env"])
            word, tone = STATE_WORDS.get(tl.state, STATE_WORDS["none"])
            with ui.element("div").classes("any-tl-summary").props('data-testid="timeline-summary"'):
                with ui.element("div").classes("any-tl-tile"):
                    text("div", "Now", "any-eyebrow")
                    text("span", word, f"any-pill pill-{tone}").props('data-testid="timeline-state"')
                with ui.element("div").classes("any-tl-tile"):
                    text("div", "Pass rate", "any-eyebrow")
                    text("div", "–" if tl.pass_rate is None else f"{tl.pass_rate}%", "any-tl-big")
                    text("div", f"of {tl.results} result{'' if tl.results == 1 else 's'}", "any-muted text-xs")
                with ui.element("div").classes("any-tl-tile"):
                    text("div", "Changes of result", "any-eyebrow")
                    text("div", str(tl.flips), "any-tl-big")
                with ui.element("div").classes("any-tl-tile"):
                    text("div", "Typical time", "any-eyebrow")
                    text("div", _seconds(tl.typical_seconds) or "–", "any-tl-big")
                    if tl.slowing:
                        text("div", "slowing down", "any-tag is-warn").props('data-testid="timeline-slowing"')
                if tl.outcomes:
                    with ui.element("div").classes("any-tl-dots").props('aria-label="Results, oldest to newest"'):
                        for o in tl.outcomes[-30:]:
                            ui.element("span").classes(f"any-tl-dot is-{o}").props(f'title="{o}"')
            with ui.element("div").classes("any-tl-actions"):
                if tl.quarantine:
                    text("div", f"Quarantined since {tl.quarantine.get('since', '?')}: "
                                f"{tl.quarantine.get('reason') or 'no reason given'}", "any-warn-line")

                async def flip() -> None:
                    await toggle_quarantine(tl.test_id,
                                            lambda: (body.refresh(), on_change() if on_change else None))

                ui.button("Release" if tl.quarantine else "Quarantine", color=None, on_click=flip) \
                    .props('flat no-caps data-testid="timeline-quarantine"').classes("any-btn")
                if on_open:
                    ui.button("Open in the editor", icon=icon("pencil"), color=None,
                              on_click=lambda: (dialog.close(), on_open(system, tl.test_id))) \
                        .props('flat no-caps data-testid="timeline-open"').classes("any-btn")
            if not tl.entries:
                ui.label("Nothing yet: no run of this test case in the recent history.").classes("any-muted")
                return
            with ui.element("ol").classes("any-tl-list").props('data-testid="timeline-entries"'):
                for e in tl.entries:
                    with ui.element("li").classes(f"any-tl-entry is-{e.kind}").props(f'data-kind="{e.kind}"'):
                        ui.icon(icon(KIND_ICONS.get(e.kind, "circle-check"))).classes("any-tl-icon") \
                            .props('aria-hidden="true"')
                        with ui.element("div").classes("any-tl-main"):
                            with ui.element("div").classes("any-tl-line"):
                                text("span", e.title, f"any-pill {TONE_PILL.get(e.tone, 'pill-idle')}"
                                     if e.kind == "result" else "any-tl-title")
                                where = " · ".join(x for x in (e.env, e.browser, _seconds(e.seconds), e.who) if x)
                                if where:
                                    text("span", where, "any-muted text-sm")
                            if e.detail:
                                text("div", e.detail, "any-tl-detail")
                        text("span", e.at.replace("T", " ")[:16], "any-muted text-xs any-tl-at")

        body()
    dialog.on("hide", dialog.delete)
    dialog.open()
