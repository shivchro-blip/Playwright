"""Journeys tab: the business transactions of a system, and whether each one works on each environment."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.journeys import CRITICALITY, STATE_LABELS, JourneyError, validate
from webapp.state import services
from webapp.ui.live import live
from webapp.ui.components import field, icon_button, text
from webapp.ui.theme import icon

PILL = {"passing": "passed", "failing": "failed", "incomplete": "warn", "stale": "warn", "not_run": "idle"}
CRIT_PILL = {"critical": "failed", "high": "warn", "normal": "idle"}


def _pill(state: str, label: str | None = None, testid: str = "") -> None:
    with ui.element("span").classes(f"any-pill pill-{PILL.get(state, 'idle')}").props(
            f'data-testid="{testid}"' if testid else ""):
        ui.element("span").classes("dot").props('aria-hidden="true"')
        text("span", label or STATE_LABELS.get(state, state))


class JourneysPanel:
    def __init__(self, on_watch: Callable[[str], None] | None = None):
        self.svc = services()
        self.on_watch = on_watch
        systems = self.svc.systems.systems()
        self.system = systems[0] if systems else ""
        self.signature: tuple = ()
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("section").classes("any-card").props('aria-label="Journeys"'):
                with ui.element("div").classes("any-card-head"):
                    with ui.element("div").classes("any-head-left"):
                        text("h2", "Journeys", "any-title")
                        self.count = text("span", "", "any-count-pill")
                    with ui.element("div").classes("any-head-right"):
                        with field("System", "jr-system"):
                            ui.select(systems, value=self.system or None,
                                      on_change=lambda e: self._set_system(e.value)) \
                                .props('for="jr-system" data-testid="journeys-system"').classes("w-44")
                        ui.button("New journey", icon=icon("plus"), color=None, on_click=lambda: self.edit(None)) \
                            .props('data-testid="journey-new"').classes("any-primary")
                with ui.element("div").classes("any-jr-intro"):
                    ui.label("A journey is a business transaction (a registration, a filing, a renewal) and the test "
                             "cases that prove it. Its status on each environment comes from the latest result of "
                             "each of those test cases.").classes("any-muted text-sm")
                self.body()
        live(self.svc.bus, ["run"], self._auto_refresh, when=lambda e: e.get("change") in ("saved", "deleted"))

    def _set_system(self, value: str | None) -> None:
        self.system = value or ""
        self.body.refresh()

    def _auto_refresh(self) -> None:
        sig = tuple((r.id, r.status, len(r.results)) for r in self.svc.runs.list())
        if sig != self.signature:
            self.signature = sig
            self.body.refresh()

    # ───── overview ─────

    @ui.refreshable_method
    def body(self) -> None:
        if not self.system:
            ui.label("No systems in systemData.json yet.").classes("any-muted")
            return
        envs = self.svc.systems.environments(self.system)
        rows = self.svc.journeys.overview(self.system, envs)
        self.count.text = f"{len(rows)} journey{'s' if len(rows) != 1 else ''}"
        with ui.element("div").classes("any-jr-summary").props('data-testid="journeys-summary"'):
            for env in envs:
                states = [o.statuses[env].state for o in rows]
                if not states:
                    continue
                passing, failing = states.count("passing"), states.count("failing")
                text("span", f"{env}: {passing} of {len(states)} passing" + (f", {failing} failing" if failing
                                                                              else ""), "any-jr-env")
        unassigned = self.svc.journeys.unassigned(self.system)
        if unassigned:
            text("div", f"{len(unassigned)} test case{'s are' if len(unassigned) != 1 else ' is'} in no journey: "
                        + ", ".join(unassigned[:6]) + (" ..." if len(unassigned) > 6 else ""),
                 "any-muted text-xs any-jr-pad").props('data-testid="journeys-unassigned"')
        if not rows:
            ui.label("No journeys for this system yet. Create one with New journey: name the business transaction "
                     "and pick the test cases that prove it.").classes("any-empty any-muted any-jr-pad") \
                .props('data-testid="journeys-empty"')
            return
        with ui.element("div").classes("any-table-wrap"):
            with ui.element("table").classes("any-table any-jr-table w-full").props('data-testid="journeys-table"'):
                with ui.element("thead"):
                    with ui.element("tr"):
                        for head in ("Journey", *envs, "Coverage", "Where it fails", ""):
                            text("th", head)
                with ui.element("tbody"):
                    for o in rows:
                        self._row(o, envs)

    def _row(self, o, envs: list[str]) -> None:
        j = o.journey
        with ui.element("tr").classes("is-clickable").props(f'data-testid="journey-row-{j.id}"') \
                .on("click", lambda j=j: self.detail(j.id)):
            with ui.element("td"):
                with ui.element("div").classes("any-jr-name"):
                    text("span", j.name, "any-tc-name")
                    text("span", j.criticality, f"any-pill pill-{CRIT_PILL[j.criticality]}")
                    if j.monitor_safe:
                        text("span", "monitor-safe", "any-tag").props('title="Safe to run against production"')
                text("div", " · ".join(x for x in (j.owner, f"{len(j.tests)} test case"
                                                    + ("s" if len(j.tests) != 1 else "")) if x), "any-tc-code")
            for env in envs:
                st = o.statuses[env]
                with ui.element("td"):
                    _pill(st.state, testid=f"journey-state-{j.id}-{env}")
                    if st.pass_rate is not None:
                        text("div", f"{st.pass_rate:g}% of last {st.runs_counted}", "any-muted text-xs")
            pct = o.coverage.percent
            text("td", "-" if pct is None else f"{pct:g}% of fields", "any-num")
            where = next((st.where for st in o.statuses.values() if st.where), "")
            text("td", where or "", "any-small")
            with ui.element("td").classes("text-right").on("click.stop", lambda: None):
                with ui.button("Run", icon=icon("play-solid"), color=None) \
                        .props(f'flat dense no-caps data-testid="journey-run-{j.id}"').classes("any-btn"):
                    with ui.menu():
                        for env in envs:
                            ui.menu_item(f"on {env}", on_click=lambda env=env, j=j: self.run(j.id, env))
                icon_button("pencil", f"Edit {j.name}", lambda j=j: self.edit(j.id)) \
                    .props(f'data-testid="journey-edit-{j.id}"')

    # ───── actions ─────

    def run(self, journey_id: str, env: str) -> None:
        if not guard("run", "run a journey", journey_id):
            return
        try:
            run = self.svc.start_journey(journey_id, env)
        except (JourneyError, ValueError, RuntimeError) as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("journey.run", journey_id, env=env, run=run.id)
        ui.notify(f"Running the journey on {env} ({run.id})", type="positive")
        if self.on_watch:
            self.on_watch(run.id)

    async def edit(self, journey_id: str | None) -> None:
        if not self.system:
            ui.notify("Pick a system first.", type="warning")
            return
        existing = self.svc.journeys.store.get(journey_id) if journey_id else None
        options = self.svc.journeys.test_ids(self.system)
        chosen = list(existing.tests) if existing else []
        options = sorted(set(options) | set(chosen), key=str.casefold)
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[40rem]").props(
                'role="dialog" aria-label="Journey"'):
            text("h2", f"Edit {existing.name}" if existing else f"New journey in {self.system}", "any-title")
            name = ui.input("Name", value=existing.name if existing else "",
                            placeholder="Business registration").props('data-testid="jr-name"').classes("w-full")
            with ui.element("div").classes("any-form-row"):
                crit = ui.select({c: c for c in CRITICALITY}, value=existing.criticality if existing else "normal",
                                 label="Criticality").props('data-testid="jr-crit"').classes("is-grow")
                owner = ui.input("Owner", value=existing.owner if existing else "",
                                 placeholder="Team or person").props('data-testid="jr-owner"').classes("is-grow")
            desc = ui.textarea("What the transaction is for", value=existing.description if existing else "") \
                .props('rows=2 data-testid="jr-desc"').classes("w-full")
            tests = ui.select(options, value=chosen, multiple=True, with_input=True, label="Test cases that prove it") \
                .props('use-chips data-testid="jr-tests"').classes("w-full")
            safe = ui.switch("Safe to monitor: its test cases create no real records, so they may run against "
                             "production many times a day", value=bool(existing and existing.monitor_safe)) \
                .props('data-testid="jr-monitor-safe"')
            missing = [t for t in chosen if t not in self.svc.journeys.test_ids(self.system)]
            if missing:
                ui.label("No longer in the test files: " + ", ".join(missing)).classes("any-muted text-xs")
            with ui.element("div").classes("any-dialog-actions"):
                if existing:
                    ui.button("Delete", color=None, on_click=lambda: dialog.submit("delete")) \
                        .props('data-testid="jr-delete"').classes("any-danger")
                ui.button("Cancel", color=None, on_click=lambda: dialog.submit(None)).classes("any-btn is-38")
                ui.button("Save", color=None, on_click=lambda: dialog.submit("save")) \
                    .props('data-testid="jr-save"').classes("any-primary")
        choice = await dialog
        dialog.delete()
        if choice == "delete" and existing:
            from webapp.ui.generator import confirm
            if not guard("edit", "delete a journey", existing.id) or not await confirm(
                    f"Delete the journey '{existing.name}'? Its test cases and runs are not touched.", "Delete",
                    "negative"):
                return
            self.svc.journeys.store.delete(existing.id)
            audit("journey.delete", existing.id)
            self.body.refresh()
            return
        if choice != "save" or not guard("edit", "save a journey", self.system):
            return
        try:
            journey = validate({"id": existing.id if existing and name.value.strip() == existing.name else "",
                                "name": name.value, "system": self.system, "criticality": crit.value,
                                "owner": owner.value, "description": desc.value, "tests": tests.value or [],
                                "monitor_safe": safe.value})
            saved = self.svc.journeys.store.save(journey, replaces=existing.id if existing else None)
        except JourneyError as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("journey.save", saved.id, tests=len(saved.tests))
        ui.notify(f"Saved {saved.name}", type="positive")
        self.body.refresh()

    def detail(self, journey_id: str) -> None:
        found = next((o for o in self.svc.journeys.overview(self.system) if o.journey.id == journey_id), None)
        if found is None:
            return
        j = found.journey
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
                'role="dialog" aria-label="Journey details" data-testid="journey-detail"'):
            with ui.element("div").classes("any-details-head"):
                with ui.element("div").classes("min-w-0"):
                    text("div", f"Journey · {j.criticality}", "any-eyebrow")
                    text("h2", j.name, "any-title")
                    if j.description:
                        text("div", j.description, "any-muted text-sm")
                icon_button("x", "Close", dialog.close)
            for env, st in found.statuses.items():
                with ui.element("section").classes("any-jr-env-block"):
                    with ui.element("div").classes("any-jr-name"):
                        text("h3", env, "any-title")
                        _pill(st.state)
                        if st.pass_rate is not None:
                            text("span", f"{st.pass_rate:g}% of the last {st.runs_counted} runs passed",
                                 "any-muted text-xs")
                    with ui.element("table").classes("any-table is-compact w-full"):
                        with ui.element("tbody"):
                            for t in st.tests:
                                with ui.element("tr"):
                                    text("td", t.test_id, "mono")
                                    with ui.element("td"):
                                        _pill({"passed": "passing", "xfailed": "passing", "failed": "failing",
                                               "error": "failing"}.get(t.outcome, "not_run"),
                                              t.outcome or "never run")
                                    text("td", t.where or "", "any-small")
                                    with ui.element("td").classes("text-right"):
                                        if t.run_id and self.on_watch:
                                            ui.button(t.run_id, color=None,
                                                      on_click=lambda rid=t.run_id: (dialog.close(),
                                                                                     self.on_watch(rid))) \
                                                .props("flat dense no-caps").classes("any-btn mono")
            cov = found.coverage
            with ui.element("section").classes("any-jr-env-block").props('data-testid="journey-coverage"'):
                text("h3", "Coverage" + ("" if cov.percent is None else f": {cov.percent:g}% of the fields "
                                                                          "on the pages it uses"), "any-title")
                ui.label("Fields the journey's steps fill or click (including the common actions and test cases "
                         "they call), out of the fields each page has.").classes("any-muted text-xs")
                for sc in cov.screens:
                    with ui.element("div").classes("any-hbar-row"):
                        text("span", sc.screen, "any-hbar-name")
                        with ui.element("div").classes("any-hbar"):
                            ui.element("i").style(f"width:{sc.percent or 0}%")
                        text("span", (f"{len(sc.used)} of {sc.total} fields" if sc.total
                                      else "page not found"), "any-hbar-val")
                if cov.missing_tests:
                    text("div", "Not in the test files: " + ", ".join(cov.missing_tests), "any-muted text-xs")
        dialog.open()
