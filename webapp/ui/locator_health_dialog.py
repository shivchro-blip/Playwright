"""Generator: check every locator of the open page against the live page in one visit."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import run, ui

from webapp.auth import audit, guard
from webapp.services.locator_check import page_url
from webapp.services.locator_health import (
    HIDDEN, LABELS, MISSING, OK, OUT_OF_RANGE, ERROR, PROBLEMS, SKIPPED, PageHealth, check_fields,
)
from webapp.services.locator_suggest import KEYS as LOCATOR_KEYS
from webapp.services.locator_suggest import signature, suggest
from webapp.state import services
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.recorder_dialog import _describe
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import PageEditor

PILLS = {OK: "passed", SKIPPED: "idle"}  # everything else is a problem and shows as failed
BROKEN = (MISSING, HIDDEN, OUT_OF_RANGE, ERROR)  # fields a replacement can be suggested for


def open_locator_health(editor: PageEditor) -> None:
    svc = services()
    gen = editor.gen
    system = gen.system
    if not system:
        ui.notify("Pick a system first", type="warning")
        return
    envs = svc.systems.environments(system)
    env_default = "dev" if "dev" in envs else (envs[0] if envs else None)
    session_file = svc.settings.workspace / "Data" / "session.json"
    state: dict = {"health": None, "busy": False}

    def url_for(env: str | None) -> str:
        login = svc.systems.login(system, env) if env else None
        return page_url(login.base_url if login else "", editor.page)

    with ui.dialog().props("persistent") as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Check all locators"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Locator health", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", "Check all locators", "any-title any-details-name")
                    text("span", editor._page_name(), "any-tc-code")
            icon_button("x", "Close", dialog.close)

        ui.label("Opens the page once in headless Chromium on this machine and resolves every field the way a run "
                 "does. A page you reach by clicking through earlier screens will show its fields as not found or "
                 "hidden: that describes the page as loaded, not always a broken locator.") \
            .classes("any-muted text-sm")
        with ui.element("div").classes("any-form-row"):
            if envs:
                with labelled("Environment", "lh-env"):
                    ui.select(envs, value=env_default,
                              on_change=lambda e: setattr(url_input, "value", url_for(e.value))) \
                        .props('for="lh-env"').classes("w-40")
            with labelled("Page URL", "lh-url").classes("is-grow"):
                url_input = ui.input(value=url_for(env_default)).props('for="lh-url" data-testid="health-url"') \
                    .classes("w-full mono-input")
        use_session = ui.switch("Use the saved login session (Data/session.json)", value=True) \
            if session_file.is_file() else None
        with ui.element("div").classes("any-dialog-actions is-start"):
            start = ui.button("Check", icon=icon("scan-search"), color=None) \
                .props('data-testid="health-start"').classes("any-primary")
            problems_only = ui.switch("Problems only", value=True,
                                      on_change=lambda: render(state["health"])) \
                .props('data-testid="health-filter"')
        result = ui.element("div").classes("flex flex-col gap-2 w-full")

        def render(health: PageHealth | None) -> None:
            result.clear()
            if health is None:
                return
            with result:
                if not health.ok:
                    with ui.element("div").classes("any-error-box"):
                        text("div", "Could not check", "any-eyebrow")
                        text("pre", health.error, "any-pre is-error")
                    return
                counts = health.counts()
                with ui.element("div").classes("any-lc-summary").props('data-testid="health-summary"'):
                    for status in (OK, *PROBLEMS, SKIPPED):
                        if counts.get(status):
                            with ui.element("span").classes(f"any-pill pill-{PILLS.get(status, 'failed')}"):
                                ui.element("span").classes("dot").props('aria-hidden="true"')
                                text("span", f"{counts[status]} {LABELS[status]}")
                    text("span", f"{health.seconds:.1f} s", "any-muted text-sm")
                text("div", f"{health.title} - {health.final_url}", "any-muted text-sm")
                if any(f.status in BROKEN for f in health.fields):
                    ui.button("Suggest fixes", icon=icon("sparkles"), color=None, on_click=lambda: do_suggest()) \
                        .props('data-testid="health-suggest"').classes("any-btn is-38") \
                        .tooltip("Scan the page and rank the elements that could replace each missing locator")
                rank = {s: i for i, s in enumerate(PROBLEMS)}
                shown = [f for f in health.fields if not problems_only.value or f.status in PROBLEMS]
                shown.sort(key=lambda f: rank.get(f.status, len(rank)))
                if not shown:
                    ui.label("No problems: every checked locator was found." if problems_only.value
                             else "No fields.").classes("any-muted text-sm")
                with ui.element("div").classes("any-scan-list").props('data-testid="health-results"'):
                    for f in shown:
                        with ui.element("div").classes("any-scan-row"):
                            with ui.element("span").classes(f"any-pill pill-{PILLS.get(f.status, 'failed')}"):
                                ui.element("span").classes("dot").props('aria-hidden="true"')
                                text("span", LABELS[f.status])
                            text("span", f.name, "mono w-52 truncate")
                            with ui.element("div").classes("min-w-0 flex-1"):
                                text("div", _describe(f.cfg), "any-small mono truncate")
                                if f.detail:
                                    text("div", f.detail, "any-muted text-xs")
                                suggestions(f.name)

        def suggestions(name: str) -> None:
            found = state.get("suggest")
            if found is None or name not in found.by_field:
                return
            options = found.by_field[name]
            if not options:
                text("div", "No element on the page looks like this field.", "any-muted text-xs")
                return
            for s in options:
                with ui.element("div").classes("any-suggest").props(f'data-testid="suggest-{name}"'):
                    text("span", f"{s.score}%", "any-pill pill-idle")
                    with ui.element("div").classes("min-w-0 flex-1"):
                        text("div", _describe(s.cfg) + (f"  \"{s.label}\"" if s.label else ""),
                             "any-small mono truncate")
                        text("div", s.reason, "any-muted text-xs")
                    ui.button("Use", color=None, on_click=lambda s=s, n=name: use(n, s)) \
                        .props(f'flat dense no-caps data-testid="suggest-use-{name}"').classes("any-btn")

        def use(name: str, s) -> None:
            if not guard("edit", "use a locator suggestion", name):
                return
            row = next((r for r in editor.rows if r.name == name), None)
            if row is None:
                ui.notify(f"{name} is no longer on this page.", type="warning")
                return
            for key in LOCATOR_KEYS:
                row.cfg.pop(key, None)
            row.cfg.update(s.cfg)
            editor.touch()
            editor.fields_view.refresh()
            audit("page.locator_suggestion", f"{system}/{editor.rel}", field=name)
            ui.notify(f"{name} now uses {_describe(s.cfg)}. Save the page to keep it.", type="positive")

        async def do_suggest() -> None:
            health = state["health"]
            if state["busy"] or health is None or not guard("check", "suggest locators"):
                return
            broken = {f.name: f.cfg for f in health.fields if f.status in BROKEN}
            taken = {signature(f.cfg) for f in health.fields if f.status == OK}
            state["busy"] = True
            try:
                storage = session_file if use_session is not None and use_session.value else None
                state["suggest"] = await run.io_bound(suggest, (url_input.value or "").strip(), broken, taken,
                                                      storage_state=storage)
                if not state["suggest"].ok:
                    ui.notify(state["suggest"].error, type="negative", multi_line=True)
                    state["suggest"] = None
                render(health)
            except Exception as e:  # e.g. Playwright's browser isn't installed
                ui.notify(f"{type(e).__name__}: {e}", type="negative", multi_line=True)
            finally:
                state["busy"] = False

        async def do_check() -> None:
            if state["busy"] or not guard("check", "check locators"):
                return
            fields = {r.name: r.cfg for r in editor.rows if r.name}
            if not fields:
                ui.notify("This page has no fields to check.", type="warning")
                return
            state["busy"] = True
            start.props("loading")
            try:
                storage = session_file if use_session is not None and use_session.value else None
                state["suggest"] = None
                state["health"] = await run.io_bound(check_fields, (url_input.value or "").strip(), fields,
                                                     storage_state=storage)
                render(state["health"])
            except Exception as e:  # e.g. Playwright's browser isn't installed
                render(PageHealth(url=url_input.value or "", error=f"{type(e).__name__}: {e}"))
            finally:
                state["busy"] = False
                start.props(remove="loading")

        start.on_click(do_check)
    dialog.open()
