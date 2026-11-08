"""Automations tab: "when X, do Y" rules (webapp/services/automations.py), their recent firings, the deploy webhook,
and the schedules and monitors that also run things on their own."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.automations import ACTIONS, OUTCOMES, WHEN, Rule, slug, validate
from webapp.services.json_files import ConflictError
from webapp.state import services
from webapp.ui.components import icon_button, text
from webapp.ui.live import live
from webapp.ui.theme import icon

CHANNELS = (("slack", "Slack"), ("teams", "Teams"), ("webhook", "Webhook"))


class AutomationsPanel:
    def __init__(self, go: Callable[[str], None] | None = None):
        self.svc = services()
        self.go = go or (lambda name: None)
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("div").classes("any-page-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h1", "Automations", "any-title is-lg")
                    ui.label("When something happens, do something: run tests after a deploy, re-run the smoke "
                             "tests when a run fails, tell a channel when a monitored journey goes down.") \
                        .classes("any-muted text-sm")
                with ui.element("div").classes("any-head-right"):
                    ui.button("New rule", icon=icon("plus"), color=None,
                              on_click=lambda: guard("schedule", "add automation") and self.edit(None)) \
                        .props('data-testid="automation-new"').classes("any-primary")
            self.body()
        live(self.svc.bus, ["workspace", "run", "deploy", "monitor"], self.body.refresh,
             when=lambda e: (e["topic"] == "workspace" and str(e.get("path", "")).endswith("automations.json"))
             or e["topic"] in ("deploy", "monitor") or e.get("change") == "finished")

    @ui.refreshable_method
    def body(self) -> None:
        engine = self.svc.automations
        rules = engine.store.list()
        recent = engine.recent(30)
        last = {}
        for item in recent:
            last.setdefault(item["rule"], item)
        with ui.element("section").classes("any-card").props('aria-label="Rules" data-testid="automation-rules"'):
            with ui.element("div").classes("any-card-head"):
                with ui.element("div").classes("any-head-left"):
                    text("h2", "Rules", "any-title")
                    text("span", str(len(rules)), "any-count-pill")
            if not rules:
                ui.label("No rules yet. New rule: pick when (a deploy, a finished run, a monitored journey going "
                         "down) and what to do.").classes("any-muted p-4").props('data-testid="automation-empty"')
            for rule in rules:
                with ui.element("div").classes("any-auto-rule").props(f'data-rule="{rule.id}"'):
                    with ui.element("div").classes("min-w-0"):
                        with ui.element("div").classes("flex items-center gap-2"):
                            text("span", rule.name, "any-auto-name")
                            if not rule.enabled:
                                text("span", "off", "any-tag")
                        text("div", rule.summary, "any-muted text-sm")
                        fired = last.get(rule.id)
                        if fired:
                            ok = all(r.get("ok") for r in fired["results"])
                            text("div", f"Last fired {fired['at'].replace('T', ' ')[:16]}"
                                        + ("" if ok else " — something failed, see Recent"),
                                 "any-small" + ("" if ok else " any-warn-line"))
                    with ui.element("div").classes("flex items-center gap-1 no-wrap"):
                        ui.switch(value=rule.enabled, on_change=lambda e, r=rule: self.toggle(r, e.value)) \
                            .props(f'dense aria-label="Rule {rule.name} on" data-testid="automation-on-{rule.id}"')
                        icon_button("pencil", "Edit rule", lambda r=rule: guard("schedule", "edit automation")
                                    and self.edit(r), "is-32")
                        icon_button("trash-2", "Delete rule", lambda r=rule: self.delete(r), "is-32") \
                            .classes("is-danger")

        url = (self.svc.settings.resolved_public_url or "http://<host>:8080").rstrip("/")
        with ui.element("section").classes("any-card any-auto-hook").props('aria-label="Deploy webhook"'):
            text("h2", "After a deploy", "any-title")
            ui.label("Have your CI/CD pipeline announce each deploy; the rules waiting for a deploy to that system and "
                     "environment fire. With sign-in on, send a token with a run permission.").classes("any-muted "
                                                                                                     "text-sm")
            text("pre", f'curl -X POST {url}/api/v1/automations/deploy -H "Content-Type: application/json" '
                        f'-H "Authorization: Bearer <token>" -d \'{{"system": "PORTAL", "env": "uat", '
                        f'"build": "482"}}\'', "any-code")

        with ui.element("section").classes("any-card").props('aria-label="Recent firings"'):
            text("h2", "Recent", "any-title")
            if not recent:
                ui.label("Nothing has fired yet.").classes("any-muted")
            with ui.element("ul").classes("any-auto-recent").props('data-testid="automation-recent"'):
                for item in recent[:15]:
                    with ui.element("li"):
                        text("span", item["at"].replace("T", " ")[:16], "any-muted text-xs")
                        text("span", item["name"], "any-auto-name")
                        ev = item.get("event") or {}
                        what = " ".join(str(x) for x in (ev.get("topic"), ev.get("change"), ev.get("build"),
                                                          ev.get("system"), ev.get("env")) if x)
                        text("span", what, "any-muted text-sm")
                        for r in item["results"]:
                            label = r["action"].replace("_", " ") + (f" → {r['run']}" if r.get("run") else "")
                            text("span", label if r.get("ok") else f"{label}: {r.get('error') or 'failed'}",
                                 "any-tag" + ("" if r.get("ok") else " is-failed"))

        with ui.element("section").classes("any-card").props('aria-label="Also automated"'):
            text("h2", "Also running on their own", "any-title")
            schedules = self.svc.schedules.list()
            monitors = self.svc.monitors.store.list()
            with ui.element("ul").classes("any-auto-recent"):
                for s in schedules:
                    with ui.element("li"):
                        text("span", "Schedule", "any-tag")
                        text("span", s.name, "any-auto-name")
                        text("span", f"{s.system}/{s.env} · {s.cron}" + ("" if s.enabled else " · off"),
                             "any-muted text-sm")
                for m in monitors:
                    with ui.element("li"):
                        text("span", "Monitor", "any-tag")
                        text("span", m.name, "any-auto-name")
                        text("span", f"{m.system}/{m.env} · every {m.every} min" + ("" if m.enabled else " · off"),
                             "any-muted text-sm")
                if not schedules and not monitors:
                    ui.label("No schedules or monitors.").classes("any-muted")
            with ui.element("div").classes("any-cf-actions"):
                ui.button("Schedules", icon=icon("calendar-clock"), color=None,
                          on_click=lambda: self.go("Schedules")).props('flat dense no-caps').classes("any-btn")
                ui.button("Monitors", icon=icon("activity"), color=None,
                          on_click=lambda: self.go("Monitors")).props('flat dense no-caps').classes("any-btn")

    def toggle(self, rule: Rule, on: bool) -> None:
        if not guard("schedule", "switch automation"):
            self.body.refresh()
            return
        rule.enabled = bool(on)
        self.svc.automations.store.save(rule)
        audit("automation.toggle", rule.id, enabled=rule.enabled)

    async def delete(self, rule: Rule) -> None:
        from webapp.ui.generator import confirm
        if not guard("schedule", "delete automation"):
            return
        if not await confirm(f"Delete the rule {rule.name!r}?", "Delete", "negative"):
            return
        self.svc.automations.store.delete(rule.id)
        audit("automation.delete", rule.id)
        self.body.refresh()

    def edit(self, rule: Rule | None) -> None:
        svc = self.svc
        systems = svc.systems.systems()
        when0 = dict(rule.when) if rule else {"on": "deploy", "system": "", "env": "", "status": "any"}
        acts = {a["type"]: a for a in (rule.do if rule else [])}
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[42rem]").props(
                f'role="dialog" aria-label="{"Edit rule" if rule else "New rule"}" data-testid="automation-dialog"'):
            text("h2", "Edit rule" if rule else "New rule", "any-title")
            name = ui.input("Name", value=rule.name if rule else "").props('data-testid="auto-name"').classes("w-full")
            text("h3", "When", "any-title is-sm")
            with ui.element("div").classes("any-form-row"):
                on = ui.select(WHEN, value=when0.get("on"), label="Something happens") \
                    .props('data-testid="auto-on"').classes("w-64")
                system = ui.select({"": "Any system", **{s: s for s in systems}}, value=when0.get("system") or "",
                                   label="System").props('data-testid="auto-system"').classes("w-40")
                env = ui.input("Environment (empty: any)", value=when0.get("env") or "") \
                    .props('data-testid="auto-env"').classes("w-48")
            status = ui.select({o: o for o in OUTCOMES}, value=when0.get("status", "any"),
                               label="Only when the run").props('data-testid="auto-status"').classes("w-48")
            status.bind_visibility_from(on, "value", backward=lambda v: v == "run_finished")
            text("h3", "Then", "any-title is-sm")
            run_tests = ui.checkbox(ACTIONS["run_tests"], value="run_tests" in acts) \
                .props('data-testid="auto-do-tests"')
            tags = ui.input("Tags (comma separated)", value=", ".join(acts.get("run_tests", {}).get("tags") or [])) \
                .props('data-testid="auto-tags"').classes("w-full")
            tags.bind_visibility_from(run_tests, "value")
            run_journeys = ui.checkbox(ACTIONS["run_journeys"] + " (all of the system's when none are listed)",
                                       value="run_journeys" in acts).props('data-testid="auto-do-journeys"')
            journeys = ui.input("Journey ids (comma separated)",
                                value=", ".join(acts.get("run_journeys", {}).get("journeys") or [])) \
                .classes("w-full")
            journeys.bind_visibility_from(run_journeys, "value")
            notify = ui.checkbox(ACTIONS["notify"], value="notify" in acts).props('data-testid="auto-do-notify"')
            had = {c.get("type") for c in acts.get("notify", {}).get("channels") or []}
            with ui.element("div").classes("flex gap-4") as channel_row:
                boxes = {kind: ui.checkbox(label, value=kind in had).props(f'data-testid="auto-ch-{kind}"')
                         for kind, label in CHANNELS}
            channel_row.bind_visibility_from(notify, "value")
            ui.label("A channel's address comes from .env (NOTIFY_SLACK_URL, NOTIFY_TEAMS_URL, NOTIFY_WEBHOOK_URL).") \
                .classes("any-muted text-xs")
            error = ui.label("").classes("any-error")

            def save() -> None:
                do = []
                if run_tests.value:
                    do.append({"type": "run_tests", "tags": [t.strip() for t in (tags.value or "").split(",")
                                                             if t.strip()]})
                if run_journeys.value:
                    do.append({"type": "run_journeys", "journeys": [j.strip() for j in (journeys.value or "")
                                                                    .split(",") if j.strip()]})
                if notify.value:
                    do.append({"type": "notify", "channels": [{"type": k} for k, b in boxes.items() if b.value]})
                item = {"id": rule.id if rule else slug(name.value or ""), "name": (name.value or "").strip(),
                        "enabled": rule.enabled if rule else True, "do": do,
                        "when": {"on": on.value, "system": system.value or "", "env": (env.value or "").strip(),
                                 "status": status.value if on.value == "run_finished" else "any"}}
                try:
                    saved = svc.automations.store.save(validate(item), new=rule is None)
                except (ValueError, ConflictError) as e:
                    error.text = str(e)
                    return
                audit("automation.save", saved.id)
                dialog.close()
                self.body.refresh()
                ui.notify(f"Saved: {saved.summary}", type="positive")

            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=dialog.close).props("flat no-caps").classes("any-btn")
                ui.button("Save", icon=icon("save"), color=None, on_click=save) \
                    .props('data-testid="auto-save"').classes("any-primary")
        dialog.on("hide", dialog.delete)
        dialog.open()
