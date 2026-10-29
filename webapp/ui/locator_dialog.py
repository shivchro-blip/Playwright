"""Generator: check a page field's locator against the live page (count, matches, highlighted screenshot)."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable

from nicegui import run, ui

from webapp.services.locator_check import LocatorCheck, check_locator, page_url
from webapp.state import services
from webapp.ui.ai_panels import ai_enabled, live_suggestions
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.theme import icon


def _session_file() -> Path:
    return services().settings.workspace / "Data" / "session.json"


def open_locator_check(system: str | None, page_name: str, page_config: dict, field_name: str, cfg: dict,
                       wait_until: str = "load", on_apply: Callable[[dict], None] | None = None) -> None:
    """cfg is the field's current (possibly unsaved) config from the editor; on_apply(locator keys) puts an
    accepted AI suggestion back into the editor (unsaved)."""
    svc = services()
    envs = svc.systems.environments(system) if system else []
    env_default = "dev" if "dev" in envs else (envs[0] if envs else None)

    def url_for(env: str | None) -> str:
        login = svc.systems.login(system, env) if system and env else None
        return page_url(login.base_url if login else "", page_config)

    state = {"busy": False}

    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Check locator"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Check locator", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", field_name or "(unnamed field)", "any-title any-details-name")
                    text("span", page_name, "any-tc-code")
            icon_button("x", "Close", dialog.close)

        with ui.element("div").classes("any-form-row"):
            if envs:
                with labelled("Environment", "lc-env"):
                    ui.select(envs, value=env_default,
                              on_change=lambda e: setattr(url_input, "value", url_for(e.value))) \
                        .props('for="lc-env"').classes("w-40")
            with labelled("Page URL", "lc-url").classes("is-grow"):
                url_input = ui.input(value=url_for(env_default)).props('for="lc-url"').classes("w-full mono-input")
        has_session = _session_file().is_file()
        session_switch = ui.switch("Use the saved login session (Data/session.json)", value=has_session) \
            if has_session else None
        text("pre", "  ".join(f"{k}={v!r}" for k, v in cfg.items() if v not in (None, "")) or "(empty config)",
             "any-pre any-cfg")

        with ui.element("div").classes("any-dialog-actions is-start"):
            check_btn = ui.button("Check", icon=icon("scan-search"), color=None).classes("any-primary")
            ui.label("Opens the page in headless Chromium on this machine.").classes("any-muted text-sm")
        result_box = ui.element("div").classes("any-lc-result")

        def render(res: LocatorCheck | None, error: str = "") -> None:
            result_box.clear()
            with result_box:
                if error or (res and res.error):
                    with ui.element("div").classes("any-error-box"):
                        text("div", "Could not check", "any-eyebrow")
                        text("pre", error or res.error, "any-pre is-error")
                    return
                if not res:
                    return
                tone = "passed" if res.count == 1 or (res.index is not None and res.index < res.count) \
                    else "failed" if res.count == 0 else "idle"
                words = "No matches" if res.count == 0 else f"{res.count} match" + ("" if res.count == 1 else "es")
                with ui.element("div").classes("any-lc-summary"):
                    with ui.element("span").classes(f"any-pill pill-{tone}").props('data-testid="lc-count"'):
                        ui.element("span").classes("dot").props('aria-hidden="true"')
                        text("span", words)
                    text("code", res.description, "any-lc-desc")
                    text("span", f"{res.seconds:.1f} s", "any-muted text-sm")
                if res.title or res.final_url:
                    text("div", f"{res.title} — {res.final_url}", "any-muted text-sm any-lc-page")
                for warning in res.warnings:
                    with ui.element("div").classes("any-callout is-inline").props('role="status"'):
                        ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
                        text("div", warning, "any-callout-text")
                if res.matches:
                    with ui.element("ol").classes("any-lc-matches"):
                        for m in res.matches:
                            with ui.element("li").classes("is-selected" if m.get("selected") else ""):
                                text("code", f"<{m['tag']}>")
                                text("span", m["text"] or "(no text)", "any-lc-text")
                                text("span", "visible" if m["visible"] else "hidden",
                                     "any-tag" + ("" if m["visible"] else " is-hidden"))
                    if res.count > len(res.matches):
                        ui.label(f"… and {res.count - len(res.matches)} more").classes("any-muted text-sm")
                if res.screenshot:
                    src = "data:image/png;base64," + base64.b64encode(res.screenshot).decode()
                    with ui.element("figure").classes("any-shot"):
                        ui.element("img").props(f'src="{src}" alt="Page with the matches outlined"')
                        text("figcaption", "Matches outlined in blue; the one index picks in red.")

        async def do_check() -> None:
            if state["busy"]:
                return
            state["busy"] = True
            check_btn.props("loading")
            try:
                storage = _session_file() if session_switch is not None and session_switch.value else None
                url = (url_input.value or "").strip()
                use_ai = ai_enabled() and on_apply is not None
                res = await run.io_bound(check_locator, url, dict(cfg), storage_state=storage,
                                         wait_until=wait_until, capture_html=use_ai)
                render(res)
                if use_ai and res.html and (res.error or res.count != 1):
                    problem = res.error or (f"The locator matches {res.count} elements" if res.count
                                            else "The locator matches nothing on the page")

                    def use(new_cfg: dict) -> None:
                        on_apply(new_cfg)
                        dialog.close()
                        ui.notify(f"{field_name}: {', '.join(f'{k}={v!r}' for k, v in new_cfg.items())} "
                                  "(not saved yet)", type="positive")

                    live_suggestions(result_box, field=field_name, cfg=dict(cfg), problem=problem, url=url,
                                     html=res.html, storage=storage, on_use=use)
            except Exception as e:  # e.g. Playwright's browser isn't installed
                render(None, f"{type(e).__name__}: {e}")
            finally:
                state["busy"] = False
                check_btn.props(remove="loading")

        check_btn.on_click(do_check)
    dialog.open()
