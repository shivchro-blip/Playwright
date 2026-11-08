"""Generator > Steps > Plain words: write the test in your own words, review what each line became (only real
fields; AI assist for what the rules could not match), check the fields on the live page, and add the steps."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import run as nrun
from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.intent import compile_lines
from webapp.services.locator_check import page_url
from webapp.ui.components import text
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import TestEditor

STATUS_TONE = {"resolved": "passed", "page": "idle", "ambiguous": "flaky", "unresolved": "failed"}
EXAMPLE = 'Open the login page\nType "ann" into user name\nClick Sign in\nPress Enter'


def _pages(svc, system: str) -> dict[str, dict[str, dict]]:
    return {r.name: svc.pom.field_configs(system, r.name) for r in svc.pom.list_pages(system) if not r.component}


def open_intent_dialog(ed: TestEditor) -> None:
    svc, system = ed.svc, ed.gen.system
    if not system:
        ui.notify("Pick a system first.", type="warning")
        return
    pages = _pages(svc, system)
    last_page = next((s.target for s in reversed(ed.steps) if s.kind == "screen" and s.target), "")
    state: dict = {"compiled": None}
    with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[56rem]").props(
            'role="dialog" aria-label="Steps in plain words" data-testid="intent-dialog"'):
        text("h2", "Steps in plain words", "any-title")
        ui.label("One step per line, in your own words: Open a page, Type \"value\" into a field, Choose \"option\" "
                 "from a field, Tick a box, Click a button, Press Enter. Each line is matched only against the "
                 "fields of this system's pages.").classes("any-muted text-sm")
        source = ui.textarea(placeholder=EXAMPLE).props('outlined autogrow data-testid="intent-text"') \
            .classes("w-full mono-input")
        with ui.element("div").classes("any-form-row items-center"):
            start = ui.select({"": "(the first line's page)", **{p: p for p in sorted(pages)}}, value=last_page or "",
                              label="Start on page").props('data-testid="intent-start"').classes("w-64")
            use_ai = ui.checkbox("Ask AI assist about lines the rules cannot match", value=svc.ai.enabled()) \
                .props('data-testid="intent-ai"')
            use_ai.set_visibility(svc.ai.enabled())
            ui.button("Read the steps", icon=icon("list-checks"), color=None, on_click=lambda: compile_now()) \
                .props('data-testid="intent-compile"').classes("any-btn is-38")
        out = ui.element("div").classes("flex flex-col gap-2 w-full")

        async def compile_now() -> None:
            out.clear()
            resolver = None
            if use_ai.value and svc.ai.enabled():
                if not guard("ai.write", "resolve steps with AI", system, system=system):
                    return
                from webapp.services.ai_assist import ai_resolver
                resolver = ai_resolver(svc.ai)
            try:
                compiled = await nrun.io_bound(compile_lines, source.value or "", pages, start.value or "", resolver)
            except Exception as e:  # noqa: BLE001 - the AI being unreachable must not lose the rules' work
                ui.notify(f"AI assist could not help: {str(e).splitlines()[0]}", type="warning")
                compiled = compile_lines(source.value or "", pages, start.value or "")
            state["compiled"] = compiled
            show(compiled)

        def show(compiled) -> None:
            with out:
                with ui.element("table").classes("any-table any-jr-table w-full").props('data-testid="intent-rows"'):
                    with ui.element("thead"):
                        with ui.element("tr"):
                            for head in ("Line", "Became", ""):
                                text("th", head)
                    with ui.element("tbody"):
                        for r in compiled.rows:
                            with ui.element("tr").props(f'data-status="{r.status}"'):
                                text("td", r.line.strip(), "any-small")
                                with ui.element("td"):
                                    if r.status == "resolved":
                                        what = {"fill": f'type "{r.value}"', "select": f'choose "{r.value}"',
                                                "press": f"press {r.value or 'Enter'}"}.get(r.action, r.action)
                                        text("span", f"{r.page}.{r.field}: {what}", "mono text-sm")
                                        if r.source == "ai":
                                            text("span", "AI", "any-tag")
                                    elif r.status == "page":
                                        text("span", f"on {r.page}", "mono text-sm")
                                    else:
                                        text("span", r.note, "any-small")
                                        if r.candidates:
                                            text("div", "Maybe: " + ", ".join(r.candidates), "any-muted text-xs")
                                with ui.element("td"):
                                    text("span", r.status, f"any-pill pill-{STATUS_TONE.get(r.status, 'idle')}")
                n = sum(len(s["do"]) for s in compiled.steps)
                text("div", f"{n} action{'' if n == 1 else 's'} on {len(compiled.steps)} page step"
                            f"{'' if len(compiled.steps) == 1 else 's'}"
                     + ("" if compiled.ok else "; lines that did not match are left out until you reword them"),
                     "any-small").props('data-testid="intent-summary"')
            add_button.set_enabled(bool(compiled.steps))
            check_button.set_enabled(bool(compiled.steps))

        async def check_live() -> None:
            compiled = state["compiled"]
            if not compiled or not guard("check", "check fields on the live page", system, system=system):
                return
            from webapp.services.locator_health import LABELS, check_fields
            env = ed.meta.get("env") or next(iter(svc.systems.environments(system)), "")
            login = svc.systems.login(system, env) if env else None
            lines = []
            for step in compiled.steps:
                screen = step["screen"]
                rel = svc.pom.screen_index(system).get(screen)
                data = svc.pom.load(system, rel)[0] if rel else {}
                url = page_url(login.base_url if login else "", data)
                fields = {i["field"]: pages[screen][i["field"]] for i in step["do"]}
                result = await nrun.io_bound(check_fields, url, fields)
                for f in result.fields:
                    lines.append(f"{screen}.{f.name}: {LABELS.get(f.status, f.status)}")
            with out:
                text("div", "On the live page (as it first loads): " + "; ".join(lines), "any-small") \
                    .props('data-testid="intent-live"')

        def add() -> None:
            compiled = state["compiled"]
            if not compiled or not compiled.steps or not guard("edit", "add steps", system, system=system):
                return
            from webapp.ui.generator import StepModel
            ed.steps.extend(StepModel.from_step(s) for s in compiled.steps)
            ed.touch()
            ed.steps_view.refresh()
            audit("intent.add", ed.meta.get("testCaseID") or "", steps=len(compiled.steps),
                  ai=sum(1 for r in compiled.rows if r.source == "ai" and r.status == "resolved"))
            dialog.close()
            ui.notify(f"Added {len(compiled.steps)} step{'' if len(compiled.steps) == 1 else 's'}; save the test "
                      "to keep them", type="positive")

        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Close", color=None, on_click=dialog.close).props("flat no-caps").classes("any-btn")
            check_button = ui.button("Check on the live page", icon=icon("scan-search"), color=None,
                                     on_click=check_live).props('data-testid="intent-check"').classes("any-btn")
            add_button = ui.button("Add to the test", icon=icon("plus"), color=None, on_click=add) \
                .props('data-testid="intent-add"').classes("any-primary")
            check_button.set_enabled(False)
            add_button.set_enabled(False)
    dialog.on("hide", dialog.delete)
    dialog.open()
