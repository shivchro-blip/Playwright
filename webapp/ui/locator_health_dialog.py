"""Generator: check every locator of the open page against the live page in one visit."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import run, ui

from webapp.auth import guard
from webapp.services.locator_check import page_url
from webapp.services.locator_health import LABELS, OK, PROBLEMS, SKIPPED, PageHealth, check_fields
from webapp.state import services
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.recorder_dialog import _describe
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import PageEditor

PILLS = {OK: "passed", SKIPPED: "idle"}  # everything else is a problem and shows as failed


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
