"""Runners: the machines that run tests for this app (see webapp/services/runners.py). Admins only."""
from __future__ import annotations

from nicegui import ui

from webapp.auth import audit, guard
from webapp.state import services
from webapp.ui.components import icon_button, text
from webapp.ui.theme import icon


def _labels(raw: str) -> list[str]:
    return [x for x in (raw or "").replace(",", " ").split() if x]


def open_runners() -> None:
    if not guard("users", "manage runners"):
        return
    svc = services()

    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Runners"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "Remote machines", "any-eyebrow")
                text("h2", "Runners", "any-title")
            icon_button("x", "Close", dialog.close)
        ui.label("A runner is a machine that runs tests for this app: start python -m webapp.runner_agent there "
                 "with its token. Pick it under Run on in Run setup, or give runners labels (windows, chrome) and "
                 "pick the label to use whichever is free.").classes("any-muted text-sm")

        @ui.refreshable
        def table() -> None:
            runners = svc.runners.list()
            running = {r.claimed_by: r.id for r in svc.runs.list() if r.status == "running" and r.claimed_by}
            if not runners:
                ui.label("No runners yet. Add one below.").classes("any-muted text-sm").props(
                    'data-testid="runners-empty"')
                return
            with ui.element("div").classes("any-table-wrap"):
                with ui.element("table").classes("any-table is-compact w-full").props('data-testid="runners-table"'):
                    with ui.element("thead"):
                        with ui.element("tr"):
                            for head in ("Runner", "Status", "Labels", "Machine", "Version", "Working on", ""):
                                text("th", head)
                    with ui.element("tbody"):
                        for r in runners:
                            with ui.element("tr").props(f'data-testid="runner-{r["name"]}"'):
                                text("td", r["name"], "any-num")
                                with ui.element("td"):
                                    text("span", "online" if r["online"] else "offline",
                                         "any-pill " + ("pill-pass" if r["online"] else "pill-warn"))
                                    if r["last_seen"]:
                                        text("span", f" seen {r['last_seen']}", "any-muted text-xs")
                                text("td", ", ".join(r["labels"]) or "none")
                                text("td", r["host"] or "not connected yet")
                                text("td", r["version"] or "")
                                text("td", running.get(r["name"], "idle" if r["online"] else ""))
                                with ui.element("td"):
                                    icon_button("tag", f"Labels of {r['name']}", lambda n=r["name"]: labels(n))
                                    icon_button("trash-2", f"Remove {r['name']}", lambda n=r["name"]: remove(n)) \
                                        .classes("is-danger")

        async def labels(name: str) -> None:
            with ui.dialog() as box, ui.card().classes("any-app any-dialog"):
                text("h2", f"Labels of {name}", "any-title")
                ui.label("Words that describe the machine, separated by spaces or commas (for example: windows "
                         "chrome lab2).").classes("any-muted text-sm")
                field = ui.input(value=" ".join(svc.runners.labels(name))).props(
                    'aria-label="Labels" data-testid="runner-labels"').classes("w-full")
                with ui.element("div").classes("any-dialog-actions"):
                    ui.button("Cancel", color=None, on_click=lambda: box.submit(False)).classes("any-btn is-38")
                    ui.button("Save", color=None, on_click=lambda: box.submit(True)) \
                        .props('data-testid="runner-labels-save"').classes("any-primary")
            if await box:
                try:
                    saved = svc.runners.set_labels(name, _labels(field.value))
                except ValueError as e:
                    ui.notify(str(e), type="negative")
                else:
                    audit("runner.labels", name, labels=saved)
                    table.refresh()
            box.delete()

        async def remove(name: str) -> None:
            from webapp.ui.generator import confirm
            if not guard("users", "remove runner", name):
                return
            busy = [r.id for r in svc.runs.list() if r.claimed_by == name and r.active]
            if busy:
                ui.notify(f"{name} is running {busy[0]}; cancel it first.", type="warning")
                return
            if not await confirm(f"Remove runner {name}? Its token stops working at once.", "Remove", "negative"):
                return
            svc.runners.remove(name)
            audit("runner.remove", name)
            table.refresh()

        table()
        ui.separator()
        text("h3", "Add a runner", "any-title")
        with ui.element("div").classes("any-form-row"):
            name = ui.input(placeholder="lab1").props('aria-label="Runner name" data-testid="runner-name"') \
                .classes("is-grow")
            tags = ui.input(placeholder="labels, e.g. windows chrome").props(
                'aria-label="Labels" data-testid="runner-new-labels"').classes("is-grow")
            ui.button("Add runner", icon=icon("plus"), color=None, on_click=lambda: add()) \
                .props('data-testid="runner-add"').classes("any-primary")
        result = ui.element("div").classes("w-full")

        def add() -> None:
            if not guard("users", "add runner", name.value or ""):
                return
            try:
                token = svc.runners.add((name.value or "").strip(), _labels(tags.value))
            except ValueError as e:
                ui.notify(str(e), type="negative")
                return
            audit("runner.add", name.value)
            created = (name.value or "").strip()
            name.value = tags.value = ""
            result.clear()
            with result:
                ui.label(f"Runner {created} added. The token is shown once; copy it now.").classes("text-sm")
                ui.code(token).props('data-testid="runner-token"').classes("w-full")
                server = svc.settings.resolved_public_url
                ui.code(f"python -m webapp.runner_agent --server {server} --token {token}") \
                    .props('data-testid="runner-command"').classes("w-full")
                if server.startswith("http://"):
                    ui.label("This address is plain http: use https for runners on other machines, or they will "
                             "refuse to connect.").classes("any-muted text-xs")
            table.refresh()
    dialog.open()
