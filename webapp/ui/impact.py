"""Impact tab: what a change reaches (test cases, journeys), what nothing tests, and running just that."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.impact import ImpactError, Report
from webapp.services.journeys import JourneyError
from webapp.state import services
from webapp.ui.components import field, text
from webapp.ui.theme import icon

CRIT_PILL = {"critical": "failed", "high": "warn", "normal": "idle"}
PRESETS = {"uncommitted": ("HEAD", "", "Changes not committed yet"),
           "last": ("HEAD~1", "HEAD", "The last commit"),
           "custom": ("", "", "Between two commits or branches")}


class ImpactPanel:
    def __init__(self, on_watch: Callable[[str], None] | None = None):
        self.svc = services()
        self.on_watch = on_watch
        self.report: Report | None = None
        self.error = ""
        systems = self.svc.systems.systems()
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("section").classes("any-card").props('aria-label="Change impact"'):
                with ui.element("div").classes("any-card-head"):
                    with ui.element("div").classes("any-head-left"):
                        text("h2", "Change impact", "any-title")
                with ui.element("div").classes("any-jr-intro"):
                    ui.label("Which test cases and journeys a change reaches: edited pages, fields, test cases, "
                             "common actions and data files (from git), or screens the developers changed in the "
                             "application. Run just those, and see the changes that nothing tests.") \
                        .classes("any-muted text-sm")
                with ui.element("div").classes("any-impact-form"):
                    with field("Changes", "imp-preset"):
                        self.preset = ui.select({k: v[2] for k, v in PRESETS.items()}, value="uncommitted",
                                                on_change=lambda e: self._preset(e.value)) \
                            .props('for="imp-preset" data-testid="impact-preset"').classes("w-64")
                    with field("Compare with", "imp-base"):
                        self.base = ui.input(value="HEAD", placeholder="origin/main") \
                            .props('for="imp-base" data-testid="impact-base"').classes("mono-input w-44")
                    with field("Up to", "imp-head"):
                        self.head = ui.input(value="", placeholder="now (files as they are)") \
                            .props('for="imp-head" data-testid="impact-head"').classes("mono-input w-44")
                    with field("Screens the app changed", "imp-system"):
                        with ui.element("div").classes("any-form-row"):
                            self.system = ui.select(systems, value=systems[0] if systems else None,
                                                    on_change=lambda _: self._pages()) \
                                .props('for="imp-system" data-testid="impact-system"').classes("w-36")
                            self.screens = ui.select([], multiple=True, with_input=True) \
                                .props('use-chips aria-label="Screens" data-testid="impact-screens"') \
                                .classes("w-72")
                    ui.button("Find impact", icon=icon("search"), color=None, on_click=self.analyze) \
                        .props('data-testid="impact-analyze"').classes("any-primary")
                self._pages()
                self.body()

    def _preset(self, key: str) -> None:
        base, head, _ = PRESETS[key]
        if key != "custom":
            self.base.value, self.head.value = base, head

    def _pages(self) -> None:
        system = self.system.value or ""
        self.screens.options = sorted({p.name for p in self.svc.pom.list_pages(system)}, key=str.casefold) \
            if system else []
        self.screens.value = []
        self.screens.update()

    def analyze(self) -> None:
        screens = {self.system.value: list(self.screens.value or [])} if self.system.value and self.screens.value \
            else {}
        try:
            self.report = self.svc.impact.report((self.base.value or "").strip(), (self.head.value or "").strip(),
                                                 screens)
            self.error = ""
        except ImpactError as e:
            self.report, self.error = None, str(e)
        self.body.refresh()

    @ui.refreshable_method
    def body(self) -> None:
        if self.error:
            text("div", self.error, "any-error text-sm any-jr-pad").props('data-testid="impact-error"')
            return
        r = self.report
        if r is None:
            return
        breaking = sum(t.breaks for t in r.tests)
        runnable = sum(len(v) for v in r.selection().values())
        with ui.element("div").classes("any-jr-summary").props('data-testid="impact-summary"'):
            text("span", f"{len(r.changes)} change{'s' if len(r.changes) != 1 else ''}", "any-jr-env")
            text("span", f"{len(r.tests)} test case{'s' if len(r.tests) != 1 else ''} reached", "any-jr-env")
            if breaking:
                text("span", f"{breaking} will break", "any-pill pill-failed")
            text("span", f"{len(r.journeys)} journey{'s' if len(r.journeys) != 1 else ''}", "any-jr-env")
            if r.not_covered:
                text("span", f"{len(r.not_covered)} not tested", "any-pill pill-warn")
        if not r.changes:
            ui.label("Nothing changed in the workspace.").classes("any-muted any-jr-pad") \
                .props('data-testid="impact-nothing"')
            return
        if runnable:
            envs = self.svc.systems.environments(next(iter(r.selection())))
            with ui.element("div").classes("any-form-row any-jr-pad"):
                env = ui.select(envs, value=envs[0] if envs else None, label="Environment") \
                    .props('data-testid="impact-env"').classes("w-40")
                ui.button(f"Run the {runnable} test case{'s' if runnable != 1 else ''}", icon=icon("play-solid"),
                          color=None, on_click=lambda: self.run(env.value)) \
                    .props('data-testid="impact-run"').classes("any-primary")
        if r.journeys:
            text("h3", "Journeys it reaches", "any-eyebrow any-jr-pad")
            with ui.element("table").classes("any-table is-compact w-full").props('data-testid="impact-journeys"'):
                with ui.element("tbody"):
                    for j in r.journeys:
                        with ui.element("tr"):
                            with ui.element("td"):
                                with ui.element("div").classes("any-jr-name"):
                                    text("span", j.name, "any-tc-name")
                                    text("span", j.criticality, f"any-pill pill-{CRIT_PILL.get(j.criticality,
                                                                                               'idle')}")
                            text("td", f"{len(j.tests)} of its {j.of} test case{'s' if j.of != 1 else ''}",
                                 "any-small")
                            text("td", ", ".join(j.tests), "mono any-small")
        if r.tests:
            text("h3", "Test cases it reaches", "any-eyebrow any-jr-pad")
            with ui.element("table").classes("any-table is-compact w-full").props('data-testid="impact-tests"'):
                with ui.element("tbody"):
                    for t in r.tests:
                        with ui.element("tr").props(f'data-testid="impact-test-{t.test_id}"'):
                            with ui.element("td"):
                                text("div", t.test_id, "mono")
                                text("div", f"{t.system} · {t.file}", "any-tc-code")
                            with ui.element("td"):
                                if t.breaks:
                                    text("span", "will break", "any-pill pill-failed")
                                if not t.runnable:
                                    text("span", "cannot run", "any-pill pill-idle") \
                                        .props('title="No tags or no steps: the framework cannot select it"')
                            text("td", "; ".join(t.reasons), "any-small")
                            text("td", ", ".join(t.journeys), "any-small")
        text("h3", "Changes", "any-eyebrow any-jr-pad")
        untested = set(r.not_covered)
        with ui.element("table").classes("any-table is-compact w-full").props('data-testid="impact-changes"'):
            with ui.element("tbody"):
                for c in r.changes:
                    with ui.element("tr"):
                        text("td", c.label)
                        with ui.element("td"):
                            if c in untested:
                                text("span", "not tested", "any-pill pill-warn") \
                                    .props('title="No test case uses this. Nothing will check the change."')
        if r.removed_tests:
            text("div", "Removed test cases: " + ", ".join(r.removed_tests), "any-muted text-xs any-jr-pad")
        if r.everything:
            text("div", "Reaches every test case of " + ", ".join(r.everything)
                 + " (system settings or a shared file changed).", "any-muted text-xs any-jr-pad")

    def run(self, env: str | None) -> None:
        if not env or self.report is None or not all(
                guard("run", "run the impacted test cases", env, system=system, env=env)
                for system in self.report.selection()):
            return
        try:
            runs = self.svc.impact.run(self.report, env)
        except (ImpactError, JourneyError, ValueError, RuntimeError) as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("impact.run", ",".join(r.id for r in runs), env=env, tests=len(self.report.tests))
        ui.notify(f"Running the impacted test cases on {env} ({', '.join(r.id for r in runs)})", type="positive")
        if self.on_watch and runs:
            self.on_watch(runs[0].id)
