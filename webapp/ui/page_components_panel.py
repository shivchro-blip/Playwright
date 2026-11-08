"""The page editor's Components section: the shared components a page uses (utils/page_components.py), whether the
page is itself shared as a component, and moving fields into a new component."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from utils import page_components
from utils.page_components import ComponentError
from webapp.services.refactor import Plan, Refactor
from webapp.ui.components import field as labelled
from webapp.ui.components import icon_button, text
from webapp.ui.theme import icon

if TYPE_CHECKING:
    from webapp.ui.generator import PageEditor


def describe(entry: dict) -> str:
    bits = []
    if entry.get("prefix"):
        bits.append(f"as {entry['prefix']}.*")
    if entry.get("within"):
        bits.append(f"inside {entry['within']}")
    return " · ".join(bits)


class ComponentsSection:
    def __init__(self, editor: PageEditor):
        self.ed = editor
        self.svc = editor.svc
        self.body()

    @property
    def system(self) -> str:
        return self.ed.gen.system

    def _own(self) -> dict:
        return {r.name.strip(): r.cfg for r in self.ed.rows if r.name.strip()}

    @ui.refreshable_method
    def body(self) -> None:
        page = self.ed.page
        name = self.ed._page_name()
        is_comp = page.get("component") is True
        try:
            entries, error = page_components.uses(page), ""
        except ComponentError as e:
            entries, error = [], str(e)
        lookup = self.svc.pom.lookup(self.system)
        with ui.element("div").classes("any-comp").props('data-testid="page-components"'):
            with ui.element("div").classes("any-section-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h3", "Components", "any-title")
                    text("span", str(len(entries)), "any-count-pill mono")
                with ui.element("div").classes("flex gap-2 items-center"):
                    ui.switch("Shared component", value=is_comp, on_change=lambda e: self.set_component(e.value)) \
                        .props('dense data-testid="page-is-component"') \
                        .tooltip("Other pages can use this page's fields")
                    ui.button("Use a component", icon=icon("plus"), color=None, on_click=self.use_dialog) \
                        .props('data-testid="component-use"').classes("any-btn is-32")
            if is_comp:
                users = self.svc.pom.users_of(self.system, name)
                text("div", f"Shared: used by {', '.join(users)}." if users else
                     "Shared: no page uses it yet. Open a page and Use a component.", "any-muted text-sm")
            if error:
                ui.label(error).classes("any-err-line")
            elif not entries and not is_comp:
                text("div", "Fields many pages have (a header, an address block) can be defined once in a component "
                            "and used here. Maintain > Move fields into a component starts one from this page.",
                     "any-muted text-sm")
            own = self._own()
            for i, entry in enumerate(entries):
                comp = lookup(entry["component"])
                with ui.element("div").classes("any-comp-row").props(f'data-component="{entry["component"]}"'):
                    with ui.element("div").classes("min-w-0"):
                        with ui.element("div").classes("flex items-center gap-2"):
                            ui.button(entry["component"], icon=icon("app-window"), color=None,
                                      on_click=lambda n=entry["component"]: self.open_component(n)) \
                                .props("flat dense no-caps").classes("any-ghost any-auto-name") \
                                .tooltip("Open the component")
                            if describe(entry):
                                text("span", describe(entry), "any-muted text-sm mono")
                        try:
                            if comp is None:
                                raise ComponentError(f"Component {entry['component']!r} not found.")
                            if not page_components.is_component(comp):
                                raise ComponentError(f"{entry['component']} is a page, not a shared component.")
                            names = [page_components.field_name(entry["prefix"], n)
                                     for n in page_components.expand_fields(comp, lookup)]
                            with ui.element("div").classes("any-comp-fields"):
                                for n in names:
                                    replaced = n in own
                                    text("span", n, "any-tag mono" + (" is-replaced" if replaced else "")) \
                                        .tooltip("This page's own field of that name replaces it" if replaced
                                                 else f"From {entry['component']}")
                        except ComponentError as e:
                            text("div", str(e), "any-err-line")
                    icon_button("x", "Stop using this component", lambda i=i: self.remove(i), "is-32")

    # ───── changes (saved with the page) ─────

    def set_component(self, on: bool) -> None:
        if on:
            self.ed.page["component"] = True
        else:
            self.ed.page.pop("component", None)
        self.ed.touch()
        self.body.refresh()

    def remove(self, i: int) -> None:
        used = list(self.ed.page.get("use") or [])
        if 0 <= i < len(used):
            used.pop(i)
        if used:
            self.ed.page["use"] = used
        else:
            self.ed.page.pop("use", None)
        self.ed.touch()
        self.body.refresh()

    async def open_component(self, name: str) -> None:
        rel = self.svc.pom.screen_index(self.system).get(name)
        if not rel:
            ui.notify(f"No component named {name!r} in {self.system}.", type="warning")
            return
        await self.ed.open(rel)

    def use_dialog(self) -> None:
        name = self.ed._page_name()
        options = [r.name for r in self.svc.pom.components(self.system) if r.name != name]
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[34rem]") \
                .props('role="dialog" aria-label="Use a component" data-testid="component-dialog"'):
            text("h2", "Use a component", "any-title")
            if not options:
                ui.label("This system has no shared components yet. Open a page with fields to share and switch on "
                         "Shared component, or use Maintain > Move fields into a component.") \
                    .classes("any-muted text-sm")
            pick = ui.select(options, value=options[0] if options else None, label="Component") \
                .props('data-testid="component-pick"').classes("w-full")
            prefix = ui.input("Prefix (optional): mailing gives mailing.street") \
                .props('data-testid="component-prefix"').classes("w-full mono-input")
            within = ui.input("Inside (optional): the container to look in, e.g. #mailing-address") \
                .props('data-testid="component-within"').classes("w-full mono-input")
            ui.label("Use one component twice on a page (two addresses) with a prefix and a container for each.") \
                .classes("any-muted text-xs")
            error = ui.label("").classes("any-error")

            def add() -> None:
                if not pick.value:
                    return
                entry = {"component": pick.value}
                if (prefix.value or "").strip():
                    entry["prefix"] = prefix.value.strip()
                if (within.value or "").strip():
                    entry["within"] = within.value.strip()
                used = [*(self.ed.page.get("use") or []), entry if len(entry) > 1 else pick.value]
                lookup = self.svc.pom.lookup(self.system)
                try:
                    page_components.expand_fields({"use": used, "fields": self._own()},
                                                  lambda n: None if n == name else lookup(n), None, (name,))
                except ComponentError as e:
                    error.text = str(e)
                    return
                self.ed.page["use"] = used
                self.ed.touch()
                dialog.close()
                self.body.refresh()

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).props("flat no-caps").classes("any-btn")
                ui.button("Add", icon=icon("plus"), color=None, on_click=add) \
                    .props('data-testid="component-add"').classes("any-primary")
        dialog.on("hide", dialog.delete)
        dialog.open()


def extract_dialog(ed: PageEditor) -> None:
    """Maintain > Move fields into a component: previewed, then all files or none."""
    if not ed._ready_for_refactor():
        return
    system, page = ed.gen.system, ed._page_name()
    names = [r.name for r in ed.rows if r.name in ed.orig_names]
    if not names:
        ui.notify("This page has no saved fields to move.", type="warning")
        return
    holder: dict = {}

    def inputs() -> None:
        with labelled("Fields to share", "ex-fields"):
            holder["fields"] = ui.select(names, multiple=True, value=[]) \
                .props('for="ex-fields" use-chips data-testid="extract-fields"').classes("w-full any-multi")
        with ui.element("div").classes("any-form-row"):
            with labelled("Component name", "ex-name").classes("is-grow"):
                holder["name"] = ui.input().props('for="ex-name" data-testid="extract-name"') \
                    .classes("w-full mono-input")
            with labelled("Folder", "ex-folder").classes("is-grow"):
                holder["folder"] = ui.input(value="components").props('for="ex-folder"').classes("w-full mono-input")

    def make_plan() -> Plan:
        r = Refactor(ed.svc.pom, ed.svc.tests)
        r.extract_component(system, page, list(holder["fields"].value or []), str(holder["name"].value or ""),
                            str(holder["folder"].value or ""))
        return r.plan()

    ed.refactor_dialog(f"Move fields of {page} into a component",
                       "The fields move to a new shared component that this page then uses. They keep their names, "
                       "so no test changes; other pages can use the component too.", inputs, make_plan,
                       "page.extract_component", f"{system}/{page}", apply_label="Move")
