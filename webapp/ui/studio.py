"""Generator Studio: author a test on the live page (webapp/services/studio.py).

Open the system's page, click what to use on its picture, name it, say what to do, and Run step: the step runs on
the live page at once and is added only when it worked. Save writes the fields to a page and the steps as a test
case, then opens it in the editor.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.locator_check import page_url
from webapp.services.recorder import RecorderError, save_recording
from webapp.services.studio import ACTIONS, VIEWPORT, Draft, Picked, Step, StudioError, StudioSession
from webapp.state import services
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.recorder_dialog import _describe
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import GeneratorPanel

NEW_FILE = "New file..."
ACTION_FOR = {"text": "fill", "textarea": "fill", "number": "fill", "select": "select", "checkbox": "check",
              "radio": "check"}
ACTION_WORDS = {"fill": "Type", "click": "Click", "select": "Choose", "check": "Tick", "press": "Press a key"}


class StudioEditor:
    def __init__(self, gen: GeneratorPanel):
        self.gen = gen
        self.svc = services()
        self.session = StudioSession()
        self.draft = Draft()
        self.picked: Picked | None = None
        self.busy = False
        ui.context.client.on_disconnect(self._closed)
        with ui.element("div").classes("any-studio"):
            with ui.element("section").classes("any-card any-studio-live").props('aria-label="Live page"'):
                with ui.element("div").classes("any-studio-bar"):
                    envs = self.svc.systems.environments(self.gen.system) if self.gen.system else []
                    self.env = ui.select(envs, value=("dev" if "dev" in envs else (envs[0] if envs else None)),
                                         on_change=lambda e: setattr(self.url, "value", self._start_url(e.value))) \
                        .props('dense aria-label="Environment" data-testid="studio-env"').classes("w-28")
                    self.url = ui.input(value=self._start_url(self.env.value)) \
                        .props('dense aria-label="Page address" data-testid="studio-url"').classes("flex-1")
                    self.open_button = ui.button("Open", icon=icon("globe"), color=None, on_click=self.open) \
                        .props('data-testid="studio-open"').classes("any-primary")
                    icon_button("rotate-cw", "Refresh the picture", self.refresh_picture)
                self.hint = ui.label("Open the page, then click what the test should use.") \
                    .classes("any-muted text-sm")
                self.picture = ui.interactive_image(on_mouse=self.on_click, events=["click"], cross=True) \
                    .props('data-testid="studio-picture"').classes("any-studio-picture")
            with ui.element("section").classes("any-card any-studio-side").props('aria-label="Steps"'):
                self.picked_view()
                self.steps_view()
                self.save_view()

    # ───── the live page ─────

    def _start_url(self, env: str | None) -> str:
        if not self.gen.system or not env:
            return ""
        login = self.svc.systems.login(self.gen.system, env)
        page = self.gen.pages.page if getattr(self.gen.pages, "rel", None) else {}
        return page_url(login.base_url if login else "", page or {})

    async def open(self) -> None:
        if self.busy or not guard("record", "open the Studio", system=self.gen.system or None):
            return
        self.busy = True
        self.open_button.props("loading")
        try:
            session_file = self.svc.settings.workspace / "Data" / "session.json"
            await self.session.start((self.url.value or "").strip(),
                                     str(session_file) if session_file.is_file() else None)
            self.draft = Draft(start_url=self.session.url)
            self.picked = None
            await self.refresh_picture()
            self.hint.text = "Click what the test should use: a box to type in, a button, a choice."
            self.picked_view.refresh()
            self.steps_view.refresh()
            self.save_view.refresh()
        except Exception as e:  # noqa: BLE001 - a bad address, no browser installed: say it
            ui.notify(f"Could not open the page: {str(e).splitlines()[0]}", type="negative")
        finally:
            self.busy = False
            self.open_button.props(remove="loading")

    async def refresh_picture(self) -> None:
        if not self.session.open:
            return
        self.picture.set_source(await self.session.screenshot())
        self.url.value = self.session.url

    async def on_click(self, e) -> None:
        if self.busy or not self.session.open:
            return
        self.busy = True
        try:
            x, y = float(e.image_x), float(e.image_y)
            taken = set(self.draft.fields) | set(self.gen.svc.pom.field_names(self.gen.system, self._page_name())
                                                  if self._page_name() else [])
            self.picked = await self.session.pick(x, y, taken)
            if self.picked is None:
                ui.notify("Nothing to use there: click a box, a button or a choice.", type="info")
            self.picked_view.refresh()
        finally:
            self.busy = False

    def _closed(self) -> None:
        if self.session.open:
            from nicegui import background_tasks
            background_tasks.create(self.session.close(), name="studio-close")

    # ───── the step being made ─────

    @ui.refreshable_method
    def picked_view(self) -> None:
        text("h3", "Step", "any-title is-sm")
        p = self.picked
        if p is None:
            text("div", "Nothing picked yet." if self.session.open else "Open the page first.", "any-muted text-sm")
            return
        known = next((name for name, cfg in self.draft.fields.items()
                      if {k: v for k, v in cfg.items() if k not in ("type", "identity")} == p.cfg), None)
        with ui.element("div").classes("any-studio-picked").props('data-testid="studio-picked"'):
            text("div", f"{p.tag} · {p.label}" if p.label else p.tag, "any-muted text-sm")
            text("div", _describe(p.cfg) + (f" ({p.matches} match, index {p.cfg.get('index')})"
                                             if p.matches > 1 else ""), "mono text-sm")
            n = len(p.identity.get("locators") or [])
            text("div", f"{n} more way{'' if n == 1 else 's'} to find it kept as its identity", "any-small")
        self.f_name = ui.input("Field name", value=known or p.name).props('data-testid="studio-name"') \
            .classes("w-full mono-input")
        default = ACTION_FOR.get(p.type, "click")
        with ui.element("div").classes("any-form-row"):
            self.f_action = ui.select({a: ACTION_WORDS[a] for a in ACTIONS}, value=default, label="Do") \
                .props('data-testid="studio-action"').classes("w-40")
            self.f_value = ui.input("Value").props('data-testid="studio-value"').classes("flex-1")
        self.f_value.bind_visibility_from(self.f_action, "value", backward=lambda a: a in ("fill", "select", "press"))
        ui.button("Run step", icon=icon("play-solid"), color=None, on_click=self.run_step) \
            .props('data-testid="studio-run"').classes("any-primary")

    async def run_step(self) -> None:
        p = self.picked
        if p is None or self.busy:
            return
        name = (self.f_name.value or "").strip()
        if not name:
            ui.notify("Give the field a name.", type="warning")
            return
        if name in self.draft.fields and {k: v for k, v in self.draft.fields[name].items()
                                          if k not in ("type", "identity")} != p.cfg:
            ui.notify(f"{name!r} is already another field in this test; choose another name.", type="warning")
            return
        step = Step(name, self.f_action.value, str(self.f_value.value or ""))
        self.busy = True
        try:
            await self.session.perform(p.cfg, step.action, step.value)
            step.ok = True
        except StudioError as e:
            step.ok, step.error = False, str(e)
        finally:
            self.busy = False
        if step.ok:
            self.draft.fields.setdefault(name, p.field)
            self.draft.steps.append(step)
            self.picked = None
            ui.notify(f"{ACTION_WORDS[step.action]} {name}: done", type="positive")
        else:
            ui.notify(f"The step did not work: {step.error}", type="negative")
        await self.refresh_picture()
        self.picked_view.refresh()
        self.steps_view.refresh()
        self.save_view.refresh()

    @ui.refreshable_method
    def steps_view(self) -> None:
        text("h3", f"Steps ({len(self.draft.steps)})", "any-title is-sm")
        if not self.draft.steps:
            text("div", "Each step you run is added here.", "any-muted text-sm")
            return
        with ui.element("ol").classes("any-studio-steps").props('data-testid="studio-steps"'):
            for i, s in enumerate(self.draft.steps):
                with ui.element("li"):
                    text("span", str(i + 1), "any-run-step-n")
                    text("span", f"{ACTION_WORDS[s.action]} {s.field}", "any-auto-name")
                    if s.value and s.action != "click":
                        text("span", s.value, "mono text-sm")
                    icon_button("x", "Remove step", lambda i=i: self.remove(i), "is-32")

    def remove(self, i: int) -> None:
        step = self.draft.steps.pop(i)
        if all(s.field != step.field for s in self.draft.steps):
            self.draft.fields.pop(step.field, None)
        self.steps_view.refresh()
        self.save_view.refresh()

    # ───── saving ─────

    def _page_name(self) -> str:
        return getattr(self, "page_input", None) and str(self.page_input.value or "").strip() or ""

    @ui.refreshable_method
    def save_view(self) -> None:
        if not self.draft.steps:
            return
        system = self.gen.system
        text("h3", "Save", "any-title is-sm")
        pages = sorted(self.svc.pom.screen_index(system))
        files = self.svc.tests.files(system)
        stamp = datetime.now().strftime("%m%d-%H%M")
        suggested = self.gen.pages.page and getattr(self.gen.pages, "rel", None) and self.gen.pages._page_name() \
            or "StudioPage"
        with labelled("Page (new, or an existing one to add fields to)", "st-page"):
            self.page_input = ui.select(pages, value=suggested, with_input=True, new_value_mode="add-unique") \
                .props('for="st-page" data-testid="studio-page"').classes("w-full")
        with labelled("Test case ID", "st-tc"):
            self.tc_input = ui.input(value=f"TC-{system}-STUDIO-{stamp}") \
                .props('for="st-tc" data-testid="studio-tc"').classes("w-full")
        with labelled("Test file", "st-file"):
            self.file_input = ui.select(files + [NEW_FILE], value=files[0] if files else NEW_FILE) \
                .props('for="st-file"').classes("w-full")
        self.new_file = ui.input("New file name", value="StudioTests.json").classes("w-full")
        self.new_file.bind_visibility_from(self.file_input, "value", value=NEW_FILE)
        self.tags_input = ui.input("Tags", value=f"{system}, studio").props('data-testid="studio-tags"') \
            .classes("w-full")
        ui.button("Save page and test case", icon=icon("save"), color=None, on_click=self.save) \
            .props('data-testid="studio-save"').classes("any-primary")

    async def save(self) -> None:
        if not guard("record", "save from the Studio", system=self.gen.system or None):
            return
        svc, system = self.svc, self.gen.system
        login = svc.systems.login(system, self.env.value) if self.env.value else None
        try:
            rel = svc.tests.normalize_file_name(self.new_file.value or "") if self.file_input.value == NEW_FILE \
                else self.file_input.value
            saved = save_recording(
                svc.pom, svc.tests, system, self.draft.recording(), page_name=self._page_name(),
                base_url=login.base_url if login else "", test_id=self.tc_input.value or "", test_file=rel,
                env=self.env.value or "", tags=[t.strip() for t in (self.tags_input.value or "").split(",")
                                                if t.strip()],
                description=f"Made in the Studio {datetime.now():%Y-%m-%d %H:%M} on {self.draft.start_url}")
        except (RecorderError, ValueError, OSError) as e:
            ui.notify(str(e), type="negative")
            return
        audit("studio.save", f"{system}/{self.tc_input.value}", test_file=saved.test_file, page=saved.page_rel)
        ui.notify(f"Saved: page {self._page_name()} and test case {self.tc_input.value} in {saved.test_file}",
                  type="positive")
        self.draft = Draft(start_url=self.session.url)
        self.steps_view.refresh()
        self.save_view.refresh()
        self.gen.tests.reload_list()
        await self.gen.open_test(saved.test_file, saved.test_index)


__all__ = ["StudioEditor", "VIEWPORT"]
