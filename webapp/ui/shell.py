"""Page shell: header with the main tabs. Each tab's content lives in its own module."""
from __future__ import annotations

from types import SimpleNamespace

from nicegui import background_tasks, ui

from webapp.auth import guard, role_summary, ui_user
from webapp.edition import LITE, NAME
from webapp.ui.ai_panels import open_ai_settings
from webapp.ui.clear_all import clear_all
from webapp.ui.control import ControlPanel
from webapp.ui.generator import GeneratorPanel
from webapp.ui.history import HistoryPanel
from webapp.ui.run_strip import RunStrip
from webapp.ui.palette import HASH_EVENT, SHORTCUT_JS, CommandPalette
from webapp.ui.trends_dialog import open_trends
from webapp.state import services
from webapp.services.commands import TABS
from webapp.ui.components import text
from webapp.ui.theme import add_head, apply_brand, icon, register_assets

if not LITE:  # the sections AutoPlay Lite leaves out (the Lite build does not ship these files)
    from webapp.ui.activity import open_activity
    from webapp.ui.app_map import MapPanel
    from webapp.ui.automations import AutomationsPanel
    from webapp.ui.confidence import ConfidencePanel
    from webapp.ui.help import HelpPanel
    from webapp.ui.impact import ImpactPanel
    from webapp.ui.journeys import JourneysPanel
    from webapp.ui.monitors import MonitorsPanel
    from webapp.ui.releases import ReleasesPanel
    from webapp.ui.runners_dialog import open_runners
    from webapp.ui.schedules import SchedulesPanel


def register() -> None:
    register_assets()

    @ui.page("/")
    def index() -> None:
        ui.page_title(NAME)
        add_head()
        apply_brand()
        # Panels own their padding (the Control tab is full-bleed)
        ui.query(".nicegui-content").classes("any-content")
        ui.add_head_html(SHORTCUT_JS)
        tab_defs = [(name, ico) for name, ico, _ in TABS]
        control_tab_ref: list = [None]  # the Control tab, once the header has made it (the run strip asks)
        with ui.element("a").classes("any-skip").props('href="#any-main" data-testid="skip-link"') \
                .on("click.prevent", lambda: ui.run_javascript("document.getElementById('any-main').focus()")):
            ui.label("Skip to main content")
        with ui.header().classes(replace="any-nav"):
            with ui.element("div").classes("any-brand"):
                with ui.element("div").classes("any-brand-mark"):
                    ui.icon(icon("play-solid")).props('aria-hidden="true"')
                ui.label(NAME)
            with ui.tabs().props('inline-label no-caps indicator-color="transparent" '
                                 'aria-label="Main sections"') as tabs:
                tab_elems = {name: ui.tab(name, icon=icon(ico)) for name, ico in tab_defs}
            strip_holder: dict = {}
            strip_holder["strip"] = RunStrip(lambda run_id: watch(run_id),
                                             lambda: tabs.value in (control_tab_ref[0], "Control"))
            with ui.button(color=None, on_click=lambda: palette.open()) \
                    .props('flat unelevated no-caps aria-label="Search everything (Ctrl+K)" '
                           'aria-keyshortcuts="Control+K Meta+K" data-testid="palette-open"') \
                    .classes("any-nav-btn any-nav-search"):
                ui.icon(icon("search")).props('aria-hidden="true"')
                ui.label("Search")
                text("kbd", "Ctrl K", "any-kbd")
            user = ui_user()
            if not LITE:
                ui.button("AI", icon=icon("sparkles"), color=None,
                          on_click=lambda: guard("settings", "ai settings") and open_ai_settings(on_ai_change)) \
                    .props('flat unelevated no-caps aria-label="AI assist settings" data-testid="ai-settings"') \
                    .classes("any-nav-btn")
            if not LITE and user and user.can("users"):
                ui.button("Runners", icon=icon("server"), color=None, on_click=open_runners) \
                    .props('flat unelevated no-caps data-testid="runners"').classes("any-nav-btn")
                ui.button("Activity", icon=icon("history"), color=None, on_click=open_activity) \
                    .props('flat unelevated no-caps data-testid="activity"').classes("any-nav-btn is-next")
            ui.button("Clear all", icon=icon("trash-2"), color=None, on_click=lambda: clear_all(after_clear_all)) \
                .props('flat unelevated no-caps aria-label="Clear all runs, logs and generated files" '
                       'data-testid="clear-all"').classes("any-nav-btn is-next any-nav-clear")
            if services().settings.login_required and user:
                with ui.element("div").classes("any-user").props('data-testid="signed-in"'):
                    text("span", user.username, "any-user-name")
                    role = text("span", user.role + (f" +{len(user.grants)}" if user.grants else ""), "any-tag") \
                        .props('data-testid="signed-in-role"')
                    role.tooltip(role_summary(user))  # the roles per system and environment, when there are any
                    with ui.element("a").classes("any-signout").props('href="/logout"'):
                        ui.label("Sign out")
            if not LITE:
                with ui.element("a").classes("any-api").props('href="/docs" target="_blank" rel="noopener"'):
                    ui.label("API")
                    ui.icon(icon("external-link")).props('aria-hidden="true"')
        control_tab = tab_elems["Control"]
        control_tab_ref[0] = control_tab

        def mark_current(name: str) -> None:
            for tab_name, tab in tab_elems.items():
                if tab_name == name:
                    tab.props('aria-current="page"')
                else:
                    tab.props(remove="aria-current")

        def on_tab(e) -> None:
            # not async: handled in order with the clicks that follow (a Generator section picked right away wins)
            mark_current(e.value)
            strip_holder["strip"].refresh()  # the strip says nothing on Control, which shows the run in full
            # the address follows the tab (#history), so a link or a reload lands on the same section; not before
            # the section the address asked for has been restored
            if restored[0]:
                ui.run_javascript(f"history.replaceState(null, '', '#{e.value.lower()}')")
            if e.value == "Generator":
                if opening[0]:
                    opening[0] = False  # a jump opens its own target; this tab change was that jump's
                else:
                    gen = panels["generator"]
                    gen.choose_default_section()
                    background_tasks.create(gen.open_default_page(), name="generator-default-page")

        tabs.on_value_change(on_tab)
        mark_current("Control")
        opening = [False]  # a palette jump into the Generator opens its own target, not the default page
        restored = [False]

        def go(name: str) -> None:
            tabs.set_value(tab_elems[name])

        def go_generator() -> None:
            """Show the Generator for a jump to something in it: its own default page must not open over that.
            The tab-change handler runs after this returns, so it is the one that clears the flag."""
            if tabs.value not in (tab_elems["Generator"], "Generator"):
                opening[0] = True
                go("Generator")

        async def run_command(command) -> None:
            args, action = command.args, command.action
            if action == "tab":
                if args.get("section") == "tests":
                    go_generator()
                    gen = panels["generator"]
                    gen.sections.set_value(gen.tests_tab)
                else:
                    go(args["name"])
            elif action in ("open_test", "open_page"):
                gen = panels["generator"]
                go_generator()
                if not await gen.use_system(args["system"]):
                    return
                if action == "open_test":
                    await gen.open_test(args["rel"], args["index"])
                else:
                    await gen.open_page(args["page"])
                    if args.get("field"):
                        gen.pages.field_filter = args["field"]
                        gen.pages.fields_view.refresh()
            elif action == "open_journey" and not LITE:
                go("Journeys")
                panels["journeys"].show_system(args["system"])
            elif action == "open_run":
                go("History")
                panels["history"].show_run(args["id"])
            elif action == "open_trends":
                open_trends()

        def go_to_hash(where) -> None:
            name = next((n for n in tab_elems if f"#{n.lower()}" == str(where or "").lower()), None)
            if name and tabs.value != tab_elems[name] and tabs.value != name:
                go(name)
            restored[0] = True  # from now on the address follows the tab

        ui.on(HASH_EVENT, lambda e: go_to_hash(e.args[0] if isinstance(e.args, list) and e.args else e.args))

        panels: dict[str, object] = {}

        def on_ai_change(enabled: bool) -> None:
            panels["generator"].set_ai(enabled)
            panels["history"].details.refresh()

        def watch(run_id: str) -> None:
            """Show a run live on the Control tab (used by Generator 'Run' and History 'Re-run')."""
            tabs.set_value(control_tab)
            panels["control"].view_run(run_id)
            panels["control"].history.refresh()

        def after_clear_all() -> None:
            """Empty what the pages still show of the runs that were deleted."""
            panels["control"].runs_cleared()
            panels["generator"].runs_cleared()
            panels["history"].runs_cleared()

        async def jump(kind: str, system: str, name: str, field: str = "") -> None:
            """Open the test case or page a paused run needs fixed, in the Generator."""
            if kind == "page":
                await run_command(SimpleNamespace(action="open_page",
                                                  args={"system": system, "page": name, "field": field}))
                return
            refs, _errors = services().tests.list_tests(system)
            ref = next((r for r in refs if r.test_id == name), None)
            if ref is None:
                ui.notify(f"{name} was not found in {system}.", type="warning")
                return
            await run_command(SimpleNamespace(action="open_test", args={"system": system, "rel": ref.file,
                                              "index": ref.index}))

        def has_unsaved() -> bool:
            return panels["generator"].has_unsaved()

        palette = CommandPalette(run_command)

        with ui.tab_panels(tabs, value=control_tab).classes("w-full bg-transparent") \
                .props('id="any-main" tabindex="-1"'):
            with ui.tab_panel(control_tab).classes("any-app"):
                panels["control"] = ControlPanel(
                    jump=jump, has_unsaved=has_unsaved,
                    on_fixed=lambda system, page, test: panels["generator"].refresh_after_fix(system, page, test))
            with ui.tab_panel(tab_elems["Generator"]).classes("any-app any-page"):
                panels["generator"] = GeneratorPanel(on_run=watch, jump=jump, go_control=lambda: go("Control"))
            with ui.tab_panel(tab_elems["History"]).classes("any-app any-page"):
                panels["history"] = HistoryPanel(on_watch=watch)
            if not LITE:
                with ui.tab_panel(tab_elems["Confidence"]).classes("any-app any-page"):
                    ConfidencePanel(go=go)
                with ui.tab_panel(tab_elems["Journeys"]).classes("any-app any-page"):
                    panels["journeys"] = JourneysPanel(on_watch=watch)
                with ui.tab_panel(tab_elems["Releases"]).classes("any-app any-page"):
                    ReleasesPanel()
                with ui.tab_panel(tab_elems["Monitors"]).classes("any-app any-page"):
                    MonitorsPanel(on_watch=watch)
                with ui.tab_panel(tab_elems["Impact"]).classes("any-app any-page"):
                    ImpactPanel(on_watch=watch)
                with ui.tab_panel(tab_elems["Map"]).classes("any-app any-page"):
                    MapPanel(open_page=lambda system, page: run_command(SimpleNamespace(
                        action="open_page", args={"system": system, "page": page})))
                with ui.tab_panel(tab_elems["Schedules"]).classes("any-app any-page"):
                    SchedulesPanel(on_watch=watch)
                with ui.tab_panel(tab_elems["Automations"]).classes("any-app any-page"):
                    AutomationsPanel(go=go)
                with ui.tab_panel(tab_elems["Help"]).classes("any-app any-page"):
                    HelpPanel()
