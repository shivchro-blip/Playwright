"""Monitors tab: journeys checked every few minutes on an environment, with uptime, incidents and alerts."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.monitors import INTERVALS, MonitorError, validate
from webapp.services.notifier import DEFAULT_ENV, channel_ready
from webapp.state import services
from webapp.ui.live import live
from webapp.ui.components import icon_button, text
from webapp.ui.theme import icon

STATE_PILL = {"up": ("passed", "Up"), "down": ("failed", "Down"), "unknown": ("warn", "Unknown"),
              "never": ("idle", "Not checked yet")}
WEBHOOKS = [("slack", "Slack"), ("teams", "Teams"), ("webhook", "Webhook")]


def _pill(state: str, testid: str = "") -> None:
    kind, label = STATE_PILL.get(state, ("idle", state))
    with ui.element("span").classes(f"any-pill pill-{kind}").props(f'data-testid="{testid}"' if testid else ""):
        ui.element("span").classes("dot").props('aria-hidden="true"')
        text("span", label)


def _pct(value) -> str:
    return "-" if value is None else f"{value:g}%"


def _when(stamp: str) -> str:
    return stamp.replace("T", " ")[:16] if stamp else "-"


class MonitorsPanel:
    def __init__(self, on_watch: Callable[[str], None] | None = None):
        self.svc = services()
        self.on_watch = on_watch
        self.signature: tuple = ()
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("section").classes("any-card").props('aria-label="Monitors"'):
                with ui.element("div").classes("any-card-head"):
                    with ui.element("div").classes("any-head-left"):
                        text("h2", "Monitors", "any-title")
                        self.count = text("span", "", "any-count-pill")
                    with ui.element("div").classes("any-head-right"):
                        ui.button("New monitor", icon=icon("plus"), color=None, on_click=lambda: self.edit(None)) \
                            .props('data-testid="monitor-new"').classes("any-primary")
                with ui.element("div").classes("any-jr-intro"):
                    ui.label("A monitor runs journeys marked safe to monitor on one environment (production, for "
                             "example) every few minutes. It tells your channels when a journey goes down and when "
                             "it recovers, and keeps its uptime and incidents.").classes("any-muted text-sm")
                self.body()
        live(self.svc.bus, ["run", "monitor"], self._auto_refresh,
             when=lambda e: e["topic"] == "monitor" or str(e.get("trigger", "")).startswith("monitor:"))

    def _auto_refresh(self) -> None:
        runs = tuple((r.id, r.status) for r in self.svc.runs.list() if r.trigger.startswith("monitor:"))
        files = tuple(sorted((p.name, p.stat().st_mtime) for p in self.svc.monitors.history_dir.glob("*.json"))) \
            if self.svc.monitors.history_dir.is_dir() else ()
        sig = (runs, files, tuple(sorted(self.svc.monitors.errors.items())))
        if sig != self.signature:
            self.signature = sig
            self.body.refresh()

    @ui.refreshable_method
    def body(self) -> None:
        monitors = self.svc.monitors.store.list()
        self.count.text = f"{len(monitors)} monitor{'s' if len(monitors) != 1 else ''}"
        if not monitors:
            ui.label("No monitors yet. First mark the journeys whose test cases create no real records as "
                     "'Safe to monitor' on the Journeys tab, then create a monitor with New monitor.") \
                .classes("any-empty any-muted any-jr-pad").props('data-testid="monitors-empty"')
            return
        for monitor in monitors:
            self._block(self.svc.monitors.summary(monitor))

    def _block(self, s: dict) -> None:
        m = s["monitor"]
        with ui.element("section").classes("any-jr-env-block any-mon").props(f'data-testid="monitor-{m["id"]}"'):
            with ui.element("div").classes("any-mon-head"):
                with ui.element("div").classes("any-jr-name"):
                    text("h3", m["name"], "any-title")
                    text("span", f'{m["system"]}/{m["env"]}', "any-tag")
                    text("span", f'every {m["every"]} min' if m["enabled"] else "paused",
                         "any-muted text-xs").props(f'data-testid="monitor-every-{m["id"]}"')
                with ui.element("div").classes("any-head-right"):
                    if s["busy"]:
                        text("span", "checking...", "any-muted text-xs").props(f'data-testid="monitor-busy-{m["id"]}"')
                    ui.button("Check now", icon=icon("play-solid"), color=None,
                              on_click=lambda mid=m["id"]: self.check(mid)) \
                        .props(f'flat dense no-caps data-testid="monitor-check-{m["id"]}"').classes("any-btn")
                    icon_button("pencil", f'Edit {m["name"]}', lambda mid=m["id"]: self.edit(mid)) \
                        .props(f'data-testid="monitor-edit-{m["id"]}"')
            facts = [f'Last check {_when(s["last_check"])}']
            if s["next_check"]:
                facts.append(f'next {_when(s["next_check"])}')
            text("div", " · ".join(facts), "any-muted text-xs")
            if s["error"]:
                text("div", f'Could not start the last check: {s["error"]}', "any-error text-sm") \
                    .props(f'data-testid="monitor-error-{m["id"]}"')
            with ui.element("table").classes("any-table is-compact w-full"):
                with ui.element("thead"):
                    with ui.element("tr"):
                        for head in ("Journey", "Now", "Since", "24h", "7 days", "30 days", "Where it fails"):
                            text("th", head)
                with ui.element("tbody"):
                    for j in s["journeys"]:
                        with ui.element("tr"):
                            text("td", j["name"])
                            with ui.element("td"):
                                _pill(j["state"], f'monitor-state-{m["id"]}-{j["id"]}')
                            text("td", _when(j["since"]), "any-small")
                            for window in ("24h", "7d", "30d"):
                                text("td", _pct(j["uptime"].get(window)), "any-num") \
                                    .props(f'data-testid="monitor-uptime-{m["id"]}-{j["id"]}-{window}"')
                            text("td", j["where"] if j["state"] == "down" else "", "any-small")
            if s["incidents"]:
                names = {j["id"]: j["name"] for j in s["journeys"]}
                text("div", "Incidents", "any-eyebrow")
                with ui.element("table").classes("any-table is-compact w-full") \
                        .props(f'data-testid="monitor-incidents-{m["id"]}"'):
                    with ui.element("tbody"):
                        for inc in s["incidents"][:8]:
                            with ui.element("tr"):
                                text("td", names.get(inc["journey"], inc["journey"]))
                                text("td", f'{_when(inc["started"])} to '
                                     + (_when(inc["ended"]) if inc["ended"] else "ongoing"), "any-small")
                                text("td", f'{inc["minutes"]} min' if inc["minutes"] is not None else "", "any-num")
                                text("td", inc["where"], "any-small")
                                with ui.element("td").classes("text-right"):
                                    if self.on_watch and inc.get("run"):
                                        ui.button(inc["run"], color=None,
                                                  on_click=lambda rid=inc["run"]: self.on_watch(rid)) \
                                            .props("flat dense no-caps").classes("any-btn mono")

    # ───── actions ─────

    def check(self, mid: str) -> None:
        if not guard("run", "run a monitor check", mid):
            return
        try:
            run = self.svc.monitors.check_now(mid)
        except MonitorError as e:
            ui.notify(str(e), type="negative")
            return
        if run is None:
            ui.notify(self.svc.monitors.errors.get(mid) or "The previous check is still running.", type="warning",
                      multi_line=True)
            self.body.refresh()
            return
        audit("monitor.check", mid, run=run.id)
        ui.notify(f"Checking now ({run.id})", type="positive")
        self.body.refresh()

    async def edit(self, mid: str | None) -> None:
        svc = self.svc
        existing = svc.monitors.store.get(mid) if mid else None
        systems = svc.systems.systems()
        if not systems:
            ui.notify("No systems in systemData.json yet.", type="warning")
            return
        channels0 = {c["type"]: c for c in (existing.channels if existing else [])}
        lookup = svc.monitors.notifier.lookup
        # at the page's top level: the view that opened it refreshes on live updates, which would remove it
        with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[40rem]").props(
                'role="dialog" aria-label="Monitor"'):
            text("h2", f"Edit {existing.name}" if existing else "New monitor", "any-title")
            name = ui.input("Name", value=existing.name if existing else "", placeholder="Production filings") \
                .props('data-testid="mon-name"').classes("w-full")
            with ui.element("div").classes("any-form-row"):
                system = ui.select(systems, value=existing.system if existing else systems[0], label="System") \
                    .props('data-testid="mon-system"').classes("is-grow")
                env = ui.select([], label="Environment").props('data-testid="mon-env"').classes("is-grow")
                every = ui.select({n: f"every {n} min" for n in INTERVALS},
                                  value=existing.every if existing else 15, label="Check") \
                    .props('data-testid="mon-every"').classes("is-grow")
            journeys = ui.select([], multiple=True, label="Journeys to watch (only those safe to monitor)") \
                .props('use-chips data-testid="mon-journeys"').classes("w-full")
            hint = ui.label("").classes("any-muted text-xs")

            def on_system(_=None) -> None:
                envs = svc.systems.environments(system.value or "")
                env.options = envs
                env.value = existing.env if existing and existing.env in envs else (
                    "prod" if "prod" in envs else (envs[-1] if envs else None))
                env.update()
                safe = {j.id: j.name for j in svc.journeys.store.list(system.value) if j.monitor_safe}
                journeys.options = safe
                journeys.value = [j for j in (existing.journeys if existing else []) if j in safe]
                journeys.update()
                hint.text = "" if safe else ("No journey of this system is marked 'Safe to monitor' yet: mark it on "
                                             "the Journeys tab (edit the journey).")

            system.on_value_change(on_system)
            on_system()

            text("h3", "Tell", "any-eyebrow")
            hooks: dict[str, tuple] = {}
            with ui.element("div").classes("any-channels"):
                for kind, label in WEBHOOKS:
                    ch = channels0.get(kind)
                    with ui.element("div").classes("any-channel"):
                        on = ui.switch(label, value=bool(ch)).props(f'data-testid="mon-ch-{kind}"')
                        with ui.element("div").classes("any-channel-cfg") as box:
                            key = ui.input(value=(ch or {}).get("url_env", DEFAULT_ENV[kind])) \
                                .props(f'aria-label="{label} URL key in .env" dense').classes("mono-input w-64")
                            ready = channel_ready({"type": kind, "url_env": key.value or ""}, lookup)
                            ui.label("set in .env" if ready else "not set in .env").classes(
                                "any-channel-status " + ("is-ok" if ready else "is-missing"))
                        box.bind_visibility_from(on, "value")
                        hooks[kind] = (on, key)
                with ui.element("div").classes("any-channel"):
                    email_on = ui.switch("Email", value="email" in channels0).props('data-testid="mon-ch-email"')
                    with ui.element("div").classes("any-channel-cfg") as box:
                        email_to = ui.input(value=", ".join(channels0.get("email", {}).get("to", [])),
                                            placeholder="oncall@example.com") \
                            .props('aria-label="Email recipients" dense').classes("w-80")
                    box.bind_visibility_from(email_on, "value")
            ui.label("Only changes are sent: when a journey goes down, and when it recovers.") \
                .classes("any-muted text-xs")
            enabled = ui.switch("Enabled", value=existing.enabled if existing else True) \
                .props('data-testid="mon-enabled"')
            with ui.element("div").classes("any-dialog-actions"):
                if existing:
                    ui.button("Delete", color=None, on_click=lambda: dialog.submit("delete")) \
                        .props('data-testid="mon-delete"').classes("any-danger")
                ui.button("Cancel", color=None, on_click=lambda: dialog.submit(None)).classes("any-btn is-38")
                ui.button("Save", color=None, on_click=lambda: dialog.submit("save")) \
                    .props('data-testid="mon-save"').classes("any-primary")
        choice = await dialog
        dialog.delete()
        if choice == "delete" and existing:
            from webapp.ui.generator import confirm
            if not guard("schedule", "delete a monitor", existing.id) or not await confirm(
                    f"Delete the monitor '{existing.name}'? Its history is kept on disk.", "Delete", "negative"):
                return
            svc.monitors.store.delete(existing.id)
            audit("monitor.delete", existing.id)
            self.body.refresh()
            return
        if choice != "save" or not guard("schedule", "save a monitor", name.value or ""):
            return
        channels = [{"type": k, "url_env": (key.value or "").strip()} for k, (on, key) in hooks.items() if on.value]
        if email_on.value:
            channels.append({"type": "email", "to": email_to.value or ""})
        try:
            monitor = validate({"id": existing.id if existing and name.value.strip() == existing.name else "",
                                "name": name.value, "system": system.value, "env": env.value, "every": every.value,
                                "journeys": journeys.value or [], "enabled": enabled.value, "channels": channels},
                               svc.journeys.store)
            saved = svc.monitors.store.save(monitor, replaces=existing.id if existing else None)
        except (MonitorError, ValueError) as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("monitor.save", saved.id, env=saved.env, journeys=len(saved.journeys))
        ui.notify(f"Saved {saved.name}", type="positive")
        self.body.refresh()
