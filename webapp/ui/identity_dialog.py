"""Generator Pages: a field's element identity (what it is, its fallback locators, how healthy it is), promoting a
fallback, and learning identities for a page's existing fields from the live page (utils/element_identity.py)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import run, ui

from utils.element_identity import identity_of, primary_of
from webapp.auth import audit, guard
from webapp.services.identity_health import FieldHealth, learn, promote
from webapp.services.locator_check import page_url
from webapp.services.page_scan import PageScan, scan_page
from webapp.state import services
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.recorder_dialog import _describe
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import FieldRow, PageEditor

TRAITS = (("tag", "Element"), ("type", "Type"), ("role", "Role"), ("id", "Id"), ("name", "Name"),
          ("testid", "Test id"), ("label", "Label"), ("aria", "ARIA label"), ("placeholder", "Placeholder"),
          ("text", "Text"))


def health_label(cfg: dict, health: FieldHealth | None) -> tuple[str, str]:
    """(short text, tone) for a field's identity button."""
    if not identity_of(cfg):
        return "No identity yet", "none"
    if health is None or health.score is None:
        return "Identity: not used in recent runs", "idle"
    tone = "good" if health.score >= 90 else ("warn" if health.score >= 60 else "bad")
    return f"Identity health {health.score}% ({health.summary})", tone


def open_identity(editor: PageEditor, row: FieldRow, health: FieldHealth | None) -> None:
    ident = identity_of(row.cfg)
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog").props(
            f'role="dialog" aria-label="Identity of {row.name}" data-testid="identity-dialog"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "Element identity", "any-eyebrow")
                text("h2", row.name, "any-title mono")
            icon_button("x", "Close", dialog.close)
        if not ident:
            ui.label("This field has no identity yet. Record it, add it with From page, or use Learn identities to "
                     "give it one: what its element looks like and other locators that find it, so a run still "
                     "finds it when its locator breaks.").classes("any-muted")
            return dialog.open()
        label, tone = health_label(row.cfg, health)
        pill = {"good": "passed", "warn": "flaky", "bad": "failed"}.get(tone, "idle")
        text("div", label, f"any-pill pill-{pill}").props('data-testid="identity-health"')
        text("h3", "What it is", "any-title is-sm")
        fp = ident.get("fingerprint") or {}
        with ui.element("dl").classes("any-kv"):
            for key, title in TRAITS:
                if fp.get(key):
                    text("dt", title)
                    text("dd", str(fp[key]), "mono")
        text("h3", "How to find it", "any-title is-sm")
        with ui.element("ol").classes("any-identity-list").props('data-testid="identity-locators"'):
            with ui.element("li"):
                text("span", _describe(primary_of(row.cfg)), "mono")
                text("span", "its locator", "any-tag")
            for cand in ident.get("locators") or []:
                with ui.element("li"):
                    text("span", _describe(cand), "mono")
                    if health and health.fallback_cfg == cand:
                        text("span", f"used {health.counts['fallback']}× recently", "any-tag is-warn")
        if ident.get("verified"):
            ui.label(f"Checked on the live page {str(ident['verified']).replace('T', ' ')[:16]}") \
                .classes("any-muted text-xs")

        def do_promote() -> None:
            if not guard("edit", "promote locator"):
                return
            new = promote(dict(row.cfg), health.fallback_cfg)
            row.cfg.clear()
            row.cfg.update(new)
            editor.touch()
            editor.fields_view.refresh()
            audit("page.identity.promote", f"{editor.gen.system}/{editor.rel}", field=row.name)
            dialog.close()
            ui.notify("Promoted: the fallback is now its locator. Save the page to keep it.", type="positive")

        def do_forget() -> None:
            row.cfg.pop("identity", None)
            editor.touch()
            editor.fields_view.refresh()
            dialog.close()

        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Forget identity", color=None, on_click=do_forget).props('flat no-caps').classes("any-btn")
            if health and health.fallback_cfg:
                ui.button("Promote the fallback it keeps using", icon=icon("arrow-up"), color=None,
                          on_click=do_promote).props('data-testid="identity-promote"').classes("any-primary")
    dialog.open()


def open_learn(editor: PageEditor) -> None:
    """Scan the live page and give the page's fields the identity of the element their locator finds there."""
    svc = services()
    system = editor.gen.system
    if not system or editor.rel is None:
        ui.notify("Open a page first", type="warning")
        return
    envs = svc.systems.environments(system)
    env_default = "dev" if "dev" in envs else (envs[0] if envs else None)
    state: dict = {"learned": [], "busy": False}

    def url_for(env: str | None) -> str:
        login = svc.systems.login(system, env) if env else None
        return page_url(login.base_url if login else "", editor.page)

    with ui.dialog().props("persistent") as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Learn identities"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "Element identity", "any-eyebrow")
                text("h2", "Learn identities from the live page", "any-title")
            icon_button("x", "Close", dialog.close)
        ui.label("Each field whose locator finds its element on the page gets an identity: what the element looks "
                 "like, and the other locators that find just it. Nothing is saved until you Save the page.") \
            .classes("any-muted text-sm")
        with ui.element("div").classes("any-form-row"):
            with labelled("Environment", "li-env"):
                ui.select(envs, value=env_default, on_change=lambda e: setattr(url_input, "value", url_for(e.value))) \
                    .props('for="li-env"').classes("w-40")
            with labelled("Page URL", "li-url").classes("is-grow"):
                url_input = ui.input(value=url_for(env_default)).props('for="li-url" data-testid="learn-url"') \
                    .classes("w-full")
            scan_button = ui.button("Scan", icon=icon("scan-search"), color=None) \
                .props('data-testid="learn-scan"').classes("any-primary")
        results = ui.element("div").props('data-testid="learn-results"')

        def render(scan: PageScan) -> None:
            results.clear()
            with results:
                if not scan.ok:
                    ui.label(scan.error or "The page could not be scanned.").classes("any-error")
                    return
                state["learned"] = learn({r.name: r.cfg for r in editor.rows}, scan.candidates)
                found = [x for x in state["learned"] if x.identity]
                text("div", f"{len(found)} of {len(state['learned'])} fields found", "any-muted")
                with ui.element("ul").classes("any-identity-list"):
                    for item in state["learned"]:
                        with ui.element("li").props(f'data-field="{item.field}"'):
                            ui.icon(icon("check" if item.identity else "minus")).props('aria-hidden="true"')
                            text("span", item.field, "mono")
                            text("span", item.note, "any-muted text-sm")
                apply_button.set_visibility(bool(found))

        def apply() -> None:
            by_name = {x.field: x.identity for x in state["learned"] if x.identity}
            for r in editor.rows:
                if r.name in by_name:
                    r.cfg["identity"] = by_name[r.name]
            editor.touch()
            editor.fields_view.refresh()
            audit("page.identity.learn", f"{system}/{editor.rel}", fields=len(by_name))
            dialog.close()
            ui.notify(f"{len(by_name)} field(s) have an identity; Save the page to keep them.", type="positive")

        with ui.element("div").classes("any-dialog-actions"):
            apply_button = ui.button("Give them these identities", icon=icon("check"), color=None, on_click=apply) \
                .props('data-testid="learn-apply"').classes("any-primary")
            apply_button.set_visibility(False)

        async def do_scan() -> None:
            if state["busy"] or not guard("check", "scan page"):
                return
            state["busy"] = True
            scan_button.props("loading")
            try:
                render(await run.io_bound(scan_page, (url_input.value or "").strip()))
            except Exception as e:  # e.g. Playwright's browser isn't installed
                render(PageScan(url=url_input.value or "", error=f"{type(e).__name__}: {e}"))
            finally:
                state["busy"] = False
                scan_button.props(remove="loading")

        scan_button.on_click(do_scan)
    dialog.open()
