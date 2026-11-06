"""Releases tab: check a release against its journeys (GO / NO-GO with the reasons), keep the evidence, sign off."""
from __future__ import annotations

from nicegui import ui

from webapp.auth import audit, guard, ui_user
from webapp.services.journeys import CRITICALITY
from webapp.services.releases import OUTCOME_LABELS, Policy, ReleaseError, outcome
from webapp.state import services
from webapp.ui.components import field, icon_button, text
from webapp.ui.theme import icon

DECISION_PILL = {"go": "passed", "no-go": "failed"}
OUTCOME_PILL = {"approved": "passed", "approved-with-risk": "warn", "rejected": "failed", "awaiting": "idle"}
STATE_PILL = {"passing": "passed", "failing": "failed", "incomplete": "warn", "stale": "warn", "not_run": "idle"}


def _pill(tone: str, label: str, testid: str = "") -> None:
    with ui.element("span").classes(f"any-pill pill-{tone}").props(f'data-testid="{testid}"' if testid else ""):
        ui.element("span").classes("dot").props('aria-hidden="true"')
        text("span", label)


class ReleasesPanel:
    def __init__(self):
        self.svc = services()
        systems = self.svc.systems.systems()
        self.system = systems[0] if systems else ""
        with ui.element("div").classes("any-page-inner is-wide"):
            with ui.element("section").classes("any-card").props('aria-label="Release checks"'):
                with ui.element("div").classes("any-card-head"):
                    with ui.element("div").classes("any-head-left"):
                        text("h2", "Release checks", "any-title")
                        self.count = text("span", "", "any-count-pill")
                    with ui.element("div").classes("any-head-right"):
                        with field("System", "rl-system"):
                            ui.select(systems, value=self.system or None,
                                      on_change=lambda e: (setattr(self, "system", e.value or ""),
                                                           self.body.refresh())) \
                                .props('for="rl-system" data-testid="releases-system"').classes("w-44")
                        ui.button("New release check", icon=icon("list-checks"), color=None,
                                  on_click=self.new_check).props('data-testid="release-new"').classes("any-primary")
                with ui.element("div").classes("any-jr-intro"):
                    ui.label("A release check decides GO or NO-GO from the journeys: every critical and high journey "
                             "must be proven working on the environment. The evidence is kept and signed, and people "
                             "approve or reject it.").classes("any-muted text-sm")
                self.body()

    @ui.refreshable_method
    def body(self) -> None:
        packs = self.svc.releases.store.list(self.system or None) if self.system else []
        self.count.text = f"{len(packs)} check{'s' if len(packs) != 1 else ''}"
        if not packs:
            ui.label("No release checks yet. Create journeys first, run their tests on the release environment, "
                     "then click New release check.").classes("any-empty any-muted any-jr-pad") \
                .props('data-testid="releases-empty"')
            return
        with ui.element("div").classes("any-table-wrap"):
            with ui.element("table").classes("any-table any-jr-table w-full").props('data-testid="releases-table"'):
                with ui.element("thead"):
                    with ui.element("tr"):
                        for head in ("Release", "Environment", "Decision", "Sign-off", "Checked", ""):
                            text("th", head)
                with ui.element("tbody"):
                    for p in packs:
                        state = outcome(p, self.svc.releases.store.signoffs(p["id"]))
                        with ui.element("tr").classes("is-clickable").props(f'data-testid="release-row-{p["id"]}"') \
                                .on("click", lambda p=p: self.detail(p["id"])):
                            with ui.element("td"):
                                text("div", p["name"], "any-tc-name")
                                text("div", f"{len(p['journeys'])} journeys", "any-tc-code")
                            text("td", p["env"])
                            with ui.element("td"):
                                _pill(DECISION_PILL[p["decision"]], p["decision"].upper())
                            with ui.element("td"):
                                _pill(OUTCOME_PILL[state], OUTCOME_LABELS[state])
                            text("td", f"{p['created_at'].replace('T', ' ')} by {p['created_by']}", "any-small")
                            with ui.element("td"):
                                verified = self.svc.releases.store.verify(p)
                                if not (verified["fingerprint_ok"] and verified["signature_ok"]):
                                    _pill("failed", "evidence changed")

    async def new_check(self) -> None:
        if not self.system or not guard("run", "check a release", self.system):
            return
        envs = self.svc.systems.environments(self.system)
        journeys = self.svc.journeys.store.list(self.system)
        if not journeys:
            ui.notify("This system has no journeys yet: create them on the Journeys tab first.", type="warning")
            return
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[40rem]").props(
                'role="dialog" aria-label="New release check"'):
            text("h2", f"Check a release of {self.system}", "any-title")
            name = ui.input("Release", placeholder="Release 4.2").props('data-testid="rc-name"').classes("w-full")
            with ui.element("div").classes("any-form-row"):
                env = ui.select(envs, value=envs[-1] if envs else None, label="Environment") \
                    .props('data-testid="rc-env"').classes("w-48")
                since = ui.input("Only results since (optional)") \
                    .props('hint="When the release was deployed, e.g. 2026-11-06T09:00" data-testid="rc-since"') \
                    .classes("is-grow")
            chosen = ui.select({j.id: f"{j.name} ({j.criticality})" for j in journeys},
                               value=[j.id for j in journeys], multiple=True, label="Journeys") \
                .props('use-chips data-testid="rc-journeys"').classes("w-full")
            with ui.element("div").classes("any-form-row"):
                block = ui.select(list(CRITICALITY), value=["critical", "high"], multiple=True,
                                  label="Journeys that block the release") \
                    .props('use-chips data-testid="rc-block"').classes("is-grow")
                age = ui.number("Evidence at most (days old)", value=7, min=1, max=365, precision=0) \
                    .props('data-testid="rc-age"').classes("w-48")
            ui.label("Run the journeys' test cases on that environment first (Journeys > Run). Set 'since' to the "
                     "time the release was deployed so only results of the release itself count.") \
                .classes("any-muted text-xs")
            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38")
                ui.button("Check", color=None, on_click=lambda: dialog.submit(True)) \
                    .props('data-testid="rc-check"').classes("any-primary")
        if not await dialog:
            dialog.delete()
            return
        dialog.delete()
        user = ui_user()
        try:
            policy = Policy.from_dict({"block": block.value or [], "max_age_days": age.value or 7,
                                       "since": since.value or ""})
            pack = self.svc.releases.create(name.value, self.system, env.value or "", chosen.value or [], policy,
                                            user.username if user else "anonymous")
        except ReleaseError as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        audit("release.check", pack["id"], decision=pack["decision"], env=pack["env"])
        self.body.refresh()
        self.detail(pack["id"])

    def detail(self, rid: str) -> None:
        store = self.svc.releases.store
        if store.get(rid) is None:
            return
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
                'role="dialog" aria-label="Release check" data-testid="release-detail"'):
            self._detail_body(rid, dialog)
        dialog.on_value_change(lambda e: None if e.value else dialog.delete())  # closed dialogs leave the page
        dialog.open()

    @ui.refreshable_method
    def _detail_body(self, rid: str, dialog) -> None:
        store = self.svc.releases.store
        p = store.get(rid)
        signoffs = store.signoffs(rid)
        verified = store.verify(p)
        state = outcome(p, signoffs)
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", f"Release check · {p['system']} / {p['env']}", "any-eyebrow")
                text("h2", p["name"], "any-title")
                text("div", f"Checked {p['created_at'].replace('T', ' ')} by {p['created_by']}",
                     "any-muted text-sm")
            with ui.element("div").classes("any-toolbar"):
                with ui.element("a").classes("any-artifact-link").props(
                        f'href="/api/releases/{rid}/report" target="_blank" rel="noopener" '
                        'data-testid="release-report"'):
                    ui.icon(icon("file-text")).props('aria-hidden="true"')
                    text("span", "Report")
                with ui.element("a").classes("any-artifact-link").props(
                        f'href="/api/releases/{rid}" target="_blank" rel="noopener" download'):
                    ui.icon(icon("download")).props('aria-hidden="true"')
                    text("span", "Evidence (JSON)")
                icon_button("x", "Close", dialog.close)
        go = p["decision"] == "go"
        with ui.element("div").classes(f"any-rl-banner {'is-go' if go else 'is-nogo'}") \
                .props('data-testid="release-decision"'):
            text("span", "GO" if go else "NO-GO", "any-rl-decision")
            text("span", "Every blocking journey is proven working." if go else
                 f"{len(p['reasons'])} blocking problem{'s' if len(p['reasons']) != 1 else ''}.")
            _pill(OUTCOME_PILL[state], OUTCOME_LABELS[state], "release-outcome")
        if p["reasons"]:
            with ui.element("ul").classes("any-rl-list").props('data-testid="release-reasons"'):
                for r in p["reasons"]:
                    text("li", r)
        if p["warnings"]:
            text("div", "Warnings", "any-eyebrow")
            with ui.element("ul").classes("any-rl-list is-warn").props('data-testid="release-warnings"'):
                for w in p["warnings"]:
                    text("li", w)
        with ui.element("table").classes("any-table is-compact w-full"):
            with ui.element("tbody"):
                for j in p["journeys"]:
                    with ui.element("tr"):
                        text("td", j["name"], "any-tc-name")
                        text("td", j["criticality"])
                        with ui.element("td"):
                            _pill(STATE_PILL.get(j["state"], "idle"), j["label"])
                        text("td", j["where"] or "", "any-small")
        ok = verified["fingerprint_ok"] and verified["signature_ok"]
        text("div", ("Evidence verified: unchanged since it was created and signed by this server. "
                     if ok else "WARNING: the evidence was changed after it was created. ")
             + f"Fingerprint {p.get('fingerprint', '')[:16]}...", "any-muted text-xs" if ok else "text-negative") \
            .props('data-testid="release-verified"')

        text("div", "Sign-off", "any-eyebrow")
        for s in signoffs:
            with ui.element("div").classes("any-scan-row").props('data-testid="release-signoff"'):
                _pill("passed" if s["decision"] == "approve" else "failed", s["decision"])
                text("span", f"{s['user']} ({s['role']})", "mono")
                text("span", s["comment"], "any-small")
                text("span", s["at"].replace("T", " "), "any-muted text-xs")
        if not signoffs:
            ui.label("Nobody has signed this check yet.").classes("any-muted text-sm")
        with ui.element("div").classes("any-form-row"):
            decision = ui.radio({"approve": "Approve", "reject": "Reject"}, value="approve").props("inline") \
                .props('data-testid="signoff-decision"')
            comment = ui.input("Why (required to reject, or to approve a NO-GO)") \
                .props('data-testid="signoff-comment"').classes("is-grow")
            ui.button("Sign", color=None, on_click=lambda: sign()).props('data-testid="signoff-sign"') \
                .classes("any-primary")

        def sign() -> None:
            if not guard("signoff", "sign off a release", rid):
                return
            user = ui_user()
            try:
                store.add_signoff(rid, user.username if user else "anonymous", user.role if user else "",
                                  decision.value, comment.value or "")
            except ReleaseError as e:
                ui.notify(str(e), type="negative", multi_line=True)
                return
            audit("release.signoff", rid, decision=decision.value)
            ui.notify("Signed.", type="positive")
            self.body.refresh()
            self._detail_body.refresh()
