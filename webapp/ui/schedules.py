"""Schedules tab: runs that start on their own (cron), and who hears about the result."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from nicegui import ui

from webapp.services.cron import Cron, CronError
from webapp.services.json_files import ConflictError
from webapp.services.notifier import DEFAULT_ENV, channel_ready
from webapp.services.schedules import Schedule, validate
from webapp.state import services
from webapp.ui.components import field, icon_button, placeholder, status_pill_template, styled_table, text
from webapp.ui.generator import confirm
from webapp.ui.theme import icon

BROWSERS = ["chrome", "msedge", "chromium", "firefox", "webkit"]
PRESETS = {"daily": "Every day", "weekdays": "Weekdays (Mon-Fri)", "weekends": "Weekends", "hours": "Every N hours",
           "custom": "Custom (cron)"}
NOTIFY = {"failure": "When a run fails", "always": "After every run", "never": "Never"}
WEBHOOKS = (("slack", "Slack"), ("teams", "Microsoft Teams"), ("webhook", "Webhook (JSON)"))
CHANNEL_NAMES = {"slack": "Slack", "teams": "Teams", "webhook": "Webhook", "email": "Email"}


def to_cron(preset: str, at: str, hours: int, custom: str) -> str:
    if preset == "custom":
        return custom.strip()
    if preset == "hours":
        minute = int(at.split(":")[1]) if re.match(r"^\d{1,2}:\d{2}$", at or "") else 0
        return f"{minute} */{max(1, min(23, int(hours or 1)))} * * *"
    m = re.match(r"^(\d{1,2}):(\d{2})$", (at or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise CronError("Enter the time as HH:MM (24-hour)")
    days = {"daily": "*", "weekdays": "1-5", "weekends": "0,6"}[preset]
    return f"{int(m.group(2))} {int(m.group(1))} * * {days}"


def from_cron(expression: str) -> tuple[str, str, int, str]:
    """(preset, HH:MM, hours, custom) for the editor."""
    parts = expression.split()
    if len(parts) == 5 and parts[0].isdigit() and parts[2] == parts[3] == "*":
        minute, hour, dow = int(parts[0]), parts[1], parts[4]
        if hour.isdigit():
            at = f"{int(hour):02d}:{minute:02d}"
            preset = {"*": "daily", "1-5": "weekdays", "0,6": "weekends", "6,0": "weekends"}.get(dow)
            if preset:
                return preset, at, 4, expression
        if hour.startswith("*/") and hour[2:].isdigit() and dow == "*":
            return "hours", f"00:{minute:02d}", int(hour[2:]), expression
    return "custom", "06:00", 4, expression


def fmt_when(dt: datetime | None) -> str:
    if not dt:
        return "—"
    today = datetime.now().date()
    day = "Today" if dt.date() == today else "Tomorrow" if (dt.date() - today).days == 1 else dt.strftime("%a %d %b")
    return f"{day} {dt:%H:%M}"


class SchedulesPanel:
    def __init__(self, on_watch: Callable[[str], None]):
        self.svc = services()
        self.on_watch = on_watch
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("section").classes("any-card").props('aria-label="Schedules"'):
                with ui.element("div").classes("any-card-head"):
                    with ui.element("div").classes("any-head-left"):
                        text("h2", "Schedules", "any-title")
                        self.count_pill = text("span", "", "any-count-pill")
                    with ui.element("div").classes("any-head-right"):
                        ui.label("Schedules fire while this app is running, in this computer's time zone.") \
                            .classes("any-muted text-sm")
                        ui.button("New schedule", icon=icon("plus"), color=None, on_click=lambda: self.edit(None)) \
                            .props('data-testid="new-schedule"').classes("any-primary")
                self.table_view()
        self.signature: tuple = ()
        ui.timer(5.0, self._auto_refresh)

    def _auto_refresh(self) -> None:
        sched = self.svc.scheduler
        sig = tuple((s.id, s.enabled, s.cron, *(r.status for r in sched.last_runs(s, 1)), s.id in sched.pending,
                     sched.errors.get(s.id, ""), str(sched.next_run(s))) for s in self.svc.schedules.list())
        if sig != self.signature:
            self.signature = sig
            self.table_view.refresh()

    @ui.refreshable_method
    def table_view(self) -> None:
        sched = self.svc.scheduler
        rows = []
        for s in self.svc.schedules.list():
            last = sched.last_runs(s, 1)
            rows.append({
                "id": s.id, "name": s.name, "target": f"{s.system}/{s.env}", "tags": ", ".join(s.tags),
                "when": Cron.parse(s.cron).describe(), "cron": s.cron, "next": fmt_when(sched.next_run(s)),
                "last_status": last[0].status if last else "", "last_id": last[0].id if last else "",
                "notify": "Off" if s.notify_when == "never" or not s.channels else
                          {"failure": "On failure", "always": "Always"}[s.notify_when] + ": "
                          + ", ".join(CHANNEL_NAMES[c["type"]] for c in s.channels),
                "enabled": s.enabled, "queued": s.id in sched.pending, "error": sched.errors.get(s.id, ""),
            })
        self.count_pill.text = f"{len(rows)} {'schedule' if len(rows) == 1 else 'schedules'}"
        if not rows:
            with ui.element("div").classes("any-empty"):
                ui.label("No schedules yet. A schedule starts a run on its own, for example the smoke tests every "
                         "weekday at 06:30, and can tell Slack, Teams or email when it fails.").classes("any-muted")
            return
        with ui.element("div").classes("any-table-wrap"):
            table = ui.table(rows=rows, row_key="id", pagination=10, columns=[
                {"name": "name", "label": "Schedule", "field": "name", "align": "left", "sortable": True},
                {"name": "when", "label": "When", "field": "when", "align": "left"},
                {"name": "next", "label": "Next run", "field": "next", "align": "left"},
                {"name": "last_status", "label": "Last run", "field": "last_status", "align": "left"},
                {"name": "notify", "label": "Notify", "field": "notify", "align": "left"},
                {"name": "actions", "label": "", "field": "id", "align": "right"},
            ]).classes("w-full is-clickable")
            styled_table(table, len(rows), wide=True)
            table.add_slot("body-cell-name", '''
                <q-td :props="props">
                  <div class="any-tc-name">{{ props.row.name }}
                    <span v-if="!props.row.enabled" class="any-tag">paused</span>
                    <span v-if="props.row.queued" class="any-tag">queued</span></div>
                  <div class="any-tc-code">{{ props.row.target }} · {{ props.row.tags }}</div>
                  <div v-if="props.row.error" class="any-msg is-fail">{{ props.row.error }}</div>
                </q-td>''')
            table.add_slot("body-cell-when", '''
                <q-td :props="props"><div>{{ props.value }}</div>
                  <div class="any-tc-code">{{ props.row.cron }}</div></q-td>''')
            table.add_slot("body-cell-next", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')
            table.add_slot("body-cell-last_status", f'''
                <q-td :props="props">
                  <template v-if="props.value">{status_pill_template()}
                    <div class="any-tc-code">{{{{ props.row.last_id }}}}</div></template>
                  <span v-else class="any-muted">—</span>
                </q-td>''')
            table.add_slot("body-cell-actions", f'''
                <q-td :props="props" @click.stop>
                  <q-btn flat dense no-caps class="any-btn is-32" :aria-label="'Run ' + props.row.name + ' now'"
                         @click="() => $parent.$emit('run_now', props.row.id)">
                    <q-icon name="{icon("circle-play")}" class="q-mr-xs" />Run now</q-btn>
                </q-td>''')
            table.on("rowClick", lambda e: self.edit(e.args[1]["id"]))
            table.on("run_now", lambda e: self.run_now(e.args))

    def run_now(self, sid: str) -> None:
        try:
            run = self.svc.scheduler.run_now(sid)
        except ValueError as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        self.table_view.refresh()
        if run is None:
            ui.notify("A run is in progress; this schedule starts as soon as it finishes.", type="info")
        else:
            ui.notify(f"Run {run.id} started", type="positive")
            self.on_watch(run.id)

    # ───── editor ─────

    def edit(self, sid: str | None) -> None:
        svc = self.svc
        existing = svc.schedules.get(sid) if sid else None
        defaults = svc.env.load()
        systems = sorted(set(svc.systems.systems()) | set(svc.catalog.systems_with_tests()))
        system0 = existing.system if existing else (defaults["systemConfig"] if defaults["systemConfig"] in systems
                                                    else (systems[0] if systems else None))
        preset0, at0, hours0, custom0 = from_cron(existing.cron if existing else "30 6 * * 1-5")
        opts0 = existing.options if existing else {"headlessMode": True}
        channels0 = {c["type"]: c for c in (existing.channels if existing else [])}

        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details any-schedule").props(
                f'role="dialog" aria-label="{"Edit schedule" if existing else "New schedule"}"'):
            with ui.element("div").classes("any-details-head"):
                with ui.element("div").classes("min-w-0"):
                    text("div", "Schedule", "any-eyebrow")
                    text("h2", existing.name if existing else "New schedule", "any-title any-details-name")
                icon_button("x", "Close", dialog.close)

            with field("Name", "sch-name"):
                name = ui.input(value=existing.name if existing else "Weekday smoke") \
                    .props('for="sch-name"').classes("w-full")

            text("h3", "What to run", "any-eyebrow")
            with ui.element("div").classes("any-form-row"):
                with field("System", "sch-system").classes("is-grow"):
                    system = ui.select(systems, value=system0, on_change=lambda e: on_system(e.value)) \
                        .props('for="sch-system"').classes("w-full")
                with field("Environment", "sch-env").classes("is-grow"):
                    env = ui.select([], value=None).props('for="sch-env"').classes("w-full")
            with ui.element("div").classes("any-form-row"):
                with field("Tags", "sch-tags").classes("is-grow"):
                    tags = ui.select([], value=[], multiple=True, with_input=True, new_value_mode="add-unique") \
                        .props('for="sch-tags" use-chips').classes("w-full")
                with field("Browsers", "sch-browsers").classes("is-grow"):
                    browser0 = existing.browsers if existing else (
                        [b.strip().lower() for b in defaults["browsers"].split(",") if b.strip()] or ["chromium"])
                    browsers = ui.select(sorted(set(BROWSERS) | set(browser0)), value=browser0, multiple=True) \
                        .props('for="sch-browsers" use-chips').classes("w-full")
            matched = ui.label("").classes("any-muted text-sm")
            with ui.element("div").classes("any-switch-row"):
                headless = ui.switch("Headless", value=bool(opts0.get("headlessMode", True)))
                tracing = ui.switch("Playwright trace", value=bool(opts0.get("tracing", False)))
                video = ui.switch("Record video", value=bool(opts0.get("recording", False)))

            def on_system(value: str | None) -> None:
                envs = svc.systems.environments(value) if value else []
                env.set_options(envs, value=(existing.env if existing and existing.env in envs
                                             else "dev" if "dev" in envs else (envs[0] if envs else None)))
                all_tags = svc.catalog.load(value).all_tags() if value else []
                keep = [t for t in (tags.value or (existing.tags if existing else [])) if t]
                tags.set_options(sorted(set(all_tags) | set(keep), key=str.casefold), value=keep)
                count_tests()

            def count_tests() -> None:
                n = len(svc.catalog.load(system.value).select(tags.value or [])) if system.value else 0
                matched.text = f"{n} test case{'s' if n != 1 else ''} match these tags."

            tags.on_value_change(lambda _: count_tests())

            text("h3", "When", "any-eyebrow")
            with ui.element("div").classes("any-form-row"):
                with field("Repeat", "sch-preset").classes("is-grow"):
                    preset = ui.select(PRESETS, value=preset0, on_change=lambda: update_when()) \
                        .props('for="sch-preset"').classes("w-full")
                with field("Time (HH:MM)", "sch-at") as at_box:
                    at = ui.input(value=at0, on_change=lambda: update_when()) \
                        .props('for="sch-at" inputmode="numeric"').classes("w-32 mono-input")
                with field("Every (hours)", "sch-hours") as hours_box:
                    hours = ui.number(value=hours0, min=1, max=23, precision=0, on_change=lambda: update_when()) \
                        .props('for="sch-hours"').classes("w-32")
                with field("Cron (minute hour day month weekday)", "sch-cron").classes("is-grow") as cron_box:
                    custom = ui.input(value=custom0, on_change=lambda: update_when()) \
                        .props('for="sch-cron"').classes("w-full mono-input")
            when_note = ui.label("").classes("any-muted text-sm").props('data-testid="sch-next"')

            def cron_value() -> str:
                return to_cron(preset.value, at.value or "", int(hours.value or 1), custom.value or "")

            def update_when() -> None:
                at_box.set_visibility(preset.value in ("daily", "weekdays", "weekends"))
                hours_box.set_visibility(preset.value == "hours")
                cron_box.set_visibility(preset.value == "custom")
                try:
                    c = Cron.parse(cron_value())
                    nxt = c.next_after(datetime.now())
                    when_note.text = f"{c.describe()} · next run {fmt_when(nxt)} · cron {c.expression}"
                    when_note.classes(remove="is-invalid")
                except CronError as e:
                    when_note.text = str(e)
                    when_note.classes(add="is-invalid")

            text("h3", "Notify", "any-eyebrow")
            with field("Send a message", "sch-notify"):
                notify_when = ui.select(NOTIFY, value=existing.notify_when if existing else "failure") \
                    .props('for="sch-notify"').classes("w-64")
            lookup = svc.scheduler.notifier.lookup
            hooks: dict[str, tuple] = {}
            with ui.element("div").classes("any-channels"):
                for kind, label in WEBHOOKS:
                    ch = channels0.get(kind)
                    with ui.element("div").classes("any-channel"):
                        on = ui.switch(label, value=bool(ch)).props(f'data-testid="ch-{kind}"')
                        with ui.element("div").classes("any-channel-cfg") as cfg_box:
                            key = ui.input(value=(ch or {}).get("url_env", DEFAULT_ENV[kind])) \
                                .props(f'aria-label="{label} URL key in .env" dense').classes("mono-input w-64")
                            status = ui.label("").classes("any-channel-status")
                        cfg_box.bind_visibility_from(on, "value")
                        hooks[kind] = (on, key, status)
                with ui.element("div").classes("any-channel"):
                    email_on = ui.switch("Email", value="email" in channels0).props('data-testid="ch-email"')
                    with ui.element("div").classes("any-channel-cfg") as email_box:
                        email_to = ui.input(value=", ".join(channels0.get("email", {}).get("to", [])),
                                            placeholder="qa@example.com, lead@example.com") \
                            .props('aria-label="Email recipients" dense').classes("w-80")
                        email_status = ui.label("").classes("any-channel-status")
                    email_box.bind_visibility_from(email_on, "value")
            ui.label("Webhook URLs and the SMTP password are secrets: put them in .env (for example "
                     "NOTIFY_SLACK_URL=https://hooks.slack.com/...); the app only shows whether they are set. "
                     "Messages include the run's failed tests and their errors.").classes("any-muted text-xs")

            def refresh_status() -> None:
                for kind, (on, key, status) in hooks.items():
                    ready = channel_ready({"type": kind, "url_env": key.value or ""}, lookup)
                    status.text = "set in .env" if ready else f"not set: add {key.value or 'NOTIFY_...'}=<url> to .env"
                    status.classes(replace="any-channel-status " + ("is-ok" if ready else "is-missing"))
                ready = channel_ready({"type": "email"}, lookup)
                email_status.text = "SMTP set in .env" if ready else "not set: add NOTIFY_SMTP_HOST (and user, " \
                                                                     "password, from) to .env"
                email_status.classes(replace="any-channel-status " + ("is-ok" if ready else "is-missing"))

            for _, key, _ in hooks.values():
                key.on_value_change(lambda _: refresh_status())

            enabled = ui.switch("Enabled", value=existing.enabled if existing else True)

            def collect() -> dict:
                channels = [{"type": k, "url_env": (key.value or "").strip()} for k, (on, key, _) in hooks.items()
                            if on.value]
                if email_on.value:
                    channels.append({"type": "email", "to": email_to.value or ""})
                return {"id": saved_id["id"], "name": name.value, "cron": cron_value(),
                        "system": system.value, "env": env.value or "", "tags": tags.value or [],
                        "browsers": browsers.value or [], "workers": existing.workers if existing else 1,
                        "options": {"headlessMode": bool(headless.value), "tracing": bool(tracing.value),
                                    "recording": bool(video.value)},
                        "test_ids": existing.test_ids if existing else [], "enabled": bool(enabled.value),
                        "notify_when": notify_when.value, "channels": channels}

            saved_id = {"id": existing.id if existing else ""}  # a test notification saves a new schedule first

            def save() -> Schedule | None:
                try:
                    schedule = svc.schedules.save(validate(collect()))
                except (ValueError, CronError, ConflictError, OSError) as e:
                    ui.notify(f"Not saved: {e}", type="negative", multi_line=True)
                    return None
                saved_id["id"] = schedule.id
                return schedule

            def save_and_close() -> None:
                if save():
                    dialog.close()
                    ui.notify("Schedule saved", type="positive")
                    self.table_view.refresh()

            def test_notification() -> None:
                schedule = save()
                if not schedule:
                    return
                if not schedule.channels:
                    ui.notify("Turn on at least one channel first.", type="warning")
                    return
                results = svc.scheduler.send_test(schedule)
                for r in results:
                    ui.notify(f"{r['channel']}: " + ("sent" if r["ok"] else r["error"]),
                              type="positive" if r["ok"] else "negative", multi_line=True)
                self.table_view.refresh()

            async def delete() -> None:
                if existing and await confirm(f"Delete the schedule '{existing.name}'? Its past runs stay in "
                                              "History.", "Delete", "negative"):
                    svc.schedules.delete(existing.id)
                    dialog.close()
                    ui.notify("Schedule deleted", type="positive")
                    self.table_view.refresh()

            with ui.element("div").classes("any-dialog-actions"):
                if existing:
                    ui.button("Delete", color=None, on_click=delete).props("flat unelevated no-caps") \
                        .classes("any-btn is-38 is-danger")
                ui.button("Send test notification", icon=icon("bell"), color=None, on_click=test_notification) \
                    .props('flat unelevated no-caps data-testid="sch-test"').classes("any-btn is-38")
                ui.button("Cancel", color=None, on_click=dialog.close).props("flat unelevated no-caps") \
                    .classes("any-btn is-38")
                ui.button("Save", icon=icon("save"), color=None, on_click=save_and_close) \
                    .props('data-testid="sch-save"').classes("any-primary")

            on_system(system.value)
            update_when()
            refresh_status()
            placeholder(tags, "Select tags")
        dialog.open()

