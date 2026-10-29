"""AI assist in the UI: settings, failure triage, locator suggestions, test drafts and run summaries.

Every result is a suggestion. Applying a locator or creating a drafted test takes a click, and is logged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from nicegui import run, ui

from webapp.services.ai_assist import AIError, LocatorSuggestion, Triage, draft_to_test_case, run_facts
from webapp.services.json_files import ConflictError, PathError
from webapp.services.locator_check import check_locator, count_on_html
from webapp.auth import audit, guard
from webapp.state import services
from webapp.ui.components import copy_button, icon_button, text
from webapp.ui.theme import icon

LOCATOR_KEYS = ("locator", "role", "name", "label", "placeholder", "text", "contains", "index", "nested",
                "hasTextDirty")
CATEGORY = {"locator_changed": "Locator changed", "timing": "Timing / waiting", "real_bug": "Likely a real bug",
            "environment": "Environment", "test_data": "Test data", "test_definition": "Test definition",
            "unknown": "Unclear"}
PILL = {"real_bug": "failed", "locator_changed": "running", "unknown": "idle"}


def ai_enabled() -> bool:
    return services().ai.enabled()


def _busy(button: ui.button, on: bool) -> None:
    button.props("loading") if on else button.props(remove="loading")


def _usage_note(usage) -> None:
    ui.label(f"Suggested by {usage}. Check it before acting on it.").classes("any-muted text-xs")


def _error(container, message: str) -> None:
    with container:
        with ui.element("div").classes("any-callout is-inline").props('role="alert"'):
            ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
            text("div", message, "any-callout-text")


def describe(cfg: dict) -> str:
    return "  ".join(f"{k}={cfg[k]!r}" for k in LOCATOR_KEYS if cfg.get(k) not in (None, ""))


# ───────────── settings ─────────────

def open_ai_settings(on_change: Callable[[bool], None]) -> None:
    svc = services()
    ai = svc.ai
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[40rem]").props(
            'role="dialog" aria-label="AI assist settings"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "Settings", "any-eyebrow")
                text("h2", "AI assist", "any-title")
            icon_button("x", "Close", dialog.close)
        ui.label("Claude helps triage failures, suggests locator fixes, drafts test cases from plain English and "
                 "summarizes runs. It only suggests: nothing it returns is saved or run until you click.") \
            .classes("text-sm")
        switch = ui.switch("Turn on AI assist", value=ai.enabled()).props('data-testid="ai-switch"')
        with ui.element("dl").classes("any-facts"):
            for label, value in (("Model", ai.model), ("Credentials", ai.credential_hint()),
                                 ("Audit log", str(ai.audit_path))):
                with ui.element("div").classes("any-fact is-wide"):
                    text("dt", label, "any-eyebrow")
                    text("dd", value)
        with ui.element("div").classes("any-callout is-inline"):
            ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
            text("div", "What is sent to the Anthropic API when you use it: error messages, tracebacks, the end of "
                        "the test log, the failure screenshot and DOM snapshot, page field names and locators, and "
                        "your request text. Values of credential keys from .env and token-like strings are removed "
                        "first. Nothing is sent until you click an AI action.", "any-callout-text")

        def toggle(e) -> None:
            if not guard("settings", "turn AI assist on or off"):
                return
            try:
                svc.env.save({"aiAssistEnabled": bool(e.value)})
            except (ValueError, OSError) as err:
                ui.notify(f"Could not save the setting to .env: {err}", type="negative")
                return
            ai.audit("setting", {"aiAssistEnabled": bool(e.value)})
            audit("setting", "aiAssistEnabled", value=bool(e.value))
            on_change(bool(e.value))
            ui.notify("AI assist on" if e.value else "AI assist off", type="info")

        switch.on_value_change(toggle)
    dialog.open()


# ───────────── 12 + 13: failure triage and locator fix (test details) ─────────────

def _pages_with_field(system: str, field: str) -> list[tuple[str, str, dict]]:
    """(rel, page name, field config) for every page of the system that defines `field`."""
    svc = services()
    found = []
    for name, rel in sorted(svc.pom.screen_index(system).items()):
        try:
            data, _ = svc.pom.load(system, rel)
        except (OSError, ValueError, PathError):
            continue
        cfg = (data.get("fields") or {}).get(field)
        if isinstance(cfg, dict):
            found.append((rel, name, cfg))
    return found


def apply_locator(system: str, rel: str, field: str, suggestion: dict) -> dict:
    """Replace the field's locator keys in the page file (type, autoGen, etc. are kept). Returns the new config."""
    svc = services()
    data, sha = svc.pom.load(system, rel)
    old = dict((data.get("fields") or {}).get(field) or {})
    new = {k: v for k, v in old.items() if k not in LOCATOR_KEYS}
    new.update({k: v for k, v in suggestion.items() if k in LOCATOR_KEYS})
    data["fields"][field] = new
    svc.pom.save(system, rel, data, sha)
    svc.ai.audit("accepted", {"feature": "locator_suggestions", "page": rel, "field": field, "before": old,
                              "after": new})
    return new


def triage_section(run_obj, row: dict, item) -> None:
    """The 'Explain with AI' block in the test details dialog."""
    svc = services()
    box = ui.element("section").classes("any-ai").props('aria-label="AI triage"')
    with box:
        with ui.element("div").classes("any-ai-head"):
            ui.icon(icon("sparkles")).props('aria-hidden="true"')
            text("div", "AI triage", "any-eyebrow")
            button = ui.button("Explain this failure", color=None).props(
                'flat unelevated no-caps data-testid="ai-triage"').classes("any-btn is-32")
        result = ui.element("div").classes("any-ai-body")

    images = item.by_kind("image") if item else []
    html_files = [a for a in (item.by_kind("html") if item else []) if a.name == "DOM Snapshot"]

    def local(att) -> Path:
        return Path(run_obj.results_dir) / att.url.rpartition("/")[2]

    async def explain() -> None:
        if not guard("ai.read", "explain failure"):
            return
        _busy(button, True)
        result.clear()
        try:
            shot = local(images[0]).read_bytes() if images else None
            dom = local(html_files[0]).read_text(encoding="utf-8", errors="replace") if html_files else ""
            texts = {a.name: a.text or "" for a in (item.by_kind("text") if item else [])}
            triage, usage = await run.io_bound(
                svc.ai.triage, test_id=row.get("test_id", ""), browser=row.get("browser", ""),
                outcome=row.get("outcome", ""), message=(item.message if item else "") or row.get("message", ""),
                trace=item.trace if item else "", log=item.log if item else "", attachments=texts,
                screenshot=shot, dom=dom)
        except (AIError, OSError) as e:
            _error(result, str(e))
            return
        finally:
            _busy(button, False)
        evidence = ((item.message if item else "") or row.get("message", "")) + "\n" + (item.trace if item else "")
        render_triage(result, triage, usage, run_obj, dom, evidence)

    button.on_click(explain)


def render_triage(container, triage: Triage, usage, run_obj, dom: str, evidence: str = "") -> None:
    with container:
        with ui.element("div").classes("any-lc-summary"):
            with ui.element("span").classes(f"any-pill pill-{PILL.get(triage.category, 'idle')}") \
                    .props('data-testid="ai-category"'):
                ui.element("span").classes("dot").props('aria-hidden="true"')
                text("span", CATEGORY[triage.category])
            text("span", f"{triage.confidence} confidence", "any-muted text-sm")
        text("p", triage.summary, "any-ai-summary")
        if triage.evidence:
            text("div", "Evidence", "any-eyebrow")
            with ui.element("ul").classes("any-ai-list"):
                for e in triage.evidence:
                    text("li", e)
        if triage.next_steps:
            text("div", "Next steps", "any-eyebrow")
            with ui.element("ol").classes("any-ai-list"):
                for s in triage.next_steps:
                    text("li", s)
        _usage_note(usage)
        pages = _pages_with_field(run_obj.config.system, triage.field) if triage.field else []
        # Several pages can define the field: prefer the one whose current locator shows up in the error
        pages.sort(key=lambda p: not any(str(v) in evidence for k, v in p[2].items() if k in LOCATOR_KEYS and v))
        if pages and dom and triage.category in ("locator_changed", "timing", "unknown"):
            fix_box = ui.element("div").classes("any-ai-body")
            btn = ui.button(f"Suggest a locator for '{triage.field}'", icon=icon("sparkles"), color=None) \
                .props('flat unelevated no-caps data-testid="ai-suggest"').classes("any-btn is-32")

            async def suggest() -> None:
                _busy(btn, True)
                fix_box.clear()
                rel, page_name, cfg = pages[0]
                try:
                    suggestions, use = await run.io_bound(
                        services().ai.suggest_locators, field=triage.field, config=cfg,
                        problem=triage.summary, dom=dom)
                    configs = [s.config() for s in suggestions.suggestions]
                    counts = await run.io_bound(count_on_html, dom, configs) if configs else []
                except AIError as e:
                    _error(fix_box, str(e))
                    return
                finally:
                    _busy(btn, False)
                with fix_box:
                    text("div", f"Checked against the DOM snapshot of this failure · page {page_name}",
                         "any-muted text-sm")

                def apply_to_page(s: LocatorSuggestion) -> None:
                    if not guard("ai.write", "apply locator"):
                        return
                    try:
                        new = apply_locator(run_obj.config.system, rel, triage.field, s.config())
                    except (ConflictError, PathError, ValueError, OSError) as e:
                        ui.notify(f"Not applied: {e}", type="negative")
                        return
                    audit("page.save", f"{run_obj.config.system}/{rel}", field=triage.field, source="ai suggestion")
                    ui.notify(f"{page_name}.{triage.field} is now {describe(new)}", type="positive")

                render_suggestions(fix_box, suggestions.suggestions, counts, f"Apply to {page_name}",
                                   lambda s: confirm_apply(page_name, triage.field, cfg, s, apply_to_page), use)

            btn.on_click(suggest)


def confirm_apply(page: str, field: str, old: dict, s: LocatorSuggestion, apply: Callable) -> None:
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[36rem]"):
        text("h2", f"Change {page}.{field}?", "any-title")
        with ui.element("dl").classes("any-facts"):
            for label, value in (("Now", describe(old) or "(none)"), ("New", describe(s.config()))):
                with ui.element("div").classes("any-fact is-wide"):
                    text("dt", label, "any-eyebrow")
                    text("dd", value, "mono")
        ui.label("This saves the page file. Run the test again to confirm the fix.").classes("any-muted text-sm")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
            ui.button("Apply", color=None, on_click=lambda: (dialog.close(), apply(s))) \
                .props('data-testid="ai-apply-confirm"').classes("any-primary")
    dialog.open()


def render_suggestions(container, suggestions: list[LocatorSuggestion], counts: list, use_label: str,
                       on_use: Callable[[LocatorSuggestion], None], usage) -> None:
    with container:
        if not suggestions:
            ui.label("No suggestions.").classes("any-muted")
            return
        with ui.element("ol").classes("any-ai-suggestions"):
            for s, n in zip(suggestions, counts or [None] * len(suggestions)):
                with ui.element("li").props('data-testid="ai-suggestion"'):
                    with ui.element("div").classes("any-ai-suggestion-head"):
                        text("code", describe(s.config()), "any-lc-desc")
                        ok = n == 1
                        label = ("1 match" if ok else "no matches" if n == 0 else f"{n} matches"
                                 if isinstance(n, int) else "invalid" if n is not None else "not checked")
                        with ui.element("span").classes(f"any-pill pill-{'passed' if ok else 'idle'}"):
                            ui.element("span").classes("dot").props('aria-hidden="true"')
                            text("span", label)
                        ui.button(use_label, color=None, on_click=lambda s=s: on_use(s)) \
                            .props("flat unelevated no-caps").classes("any-btn is-32")
                    text("div", s.rationale, "any-muted text-sm")
        _usage_note(usage)


# ───────────── 13: in the live locator checker ─────────────

def live_suggestions(container, *, field: str, cfg: dict, problem: str, url: str, html: str, storage,
                     on_use: Callable[[dict], None]) -> None:
    with container:
        btn = ui.button("Suggest a locator with AI", icon=icon("sparkles"), color=None) \
            .props('flat unelevated no-caps data-testid="ai-suggest-live"').classes("any-btn is-32")
        out = ui.element("div").classes("any-ai-body")

    async def suggest() -> None:
        if not guard("ai.read", "suggest locators"):
            return
        _busy(btn, True)
        out.clear()
        try:
            suggestions, usage = await run.io_bound(services().ai.suggest_locators, field=field, config=cfg,
                                                    problem=problem, dom=html, url=url)
            counts = []
            for s in suggestions.suggestions:  # checked on the live page, like the user's own check
                res = await run.io_bound(check_locator, url, s.config(), storage_state=storage)
                counts.append(res.count if res.ok else res.error[:120])
        except AIError as e:
            _error(out, str(e))
            return
        finally:
            _busy(btn, False)

        def use(s: LocatorSuggestion) -> None:
            services().ai.audit("accepted", {"feature": "locator_suggestions", "field": field, "before": cfg,
                                             "after": s.config(), "where": "generator (unsaved)"})
            on_use(s.config())

        render_suggestions(out, suggestions.suggestions, counts, "Use in editor", use, usage)

    btn.on_click(suggest)


# ───────────── 14: test drafts ─────────────

def open_draft_dialog(editor) -> None:
    """editor: generator.TestEditor (uses its system, file dialog and create)."""
    svc = services()
    system = editor.gen.system
    if not system:
        ui.notify("Pick a system first", type="warning")
        return
    state: dict = {"tc": None}
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Draft a test with AI"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "AI assist", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", "Draft a test case", "any-title any-details-name")
                    text("span", system, "any-tc-code")
            icon_button("x", "Close", dialog.close)
        ui.label("Describe the flow in plain English. Claude only uses this system's pages and fields; the draft is "
                 "checked and shown here, and nothing is saved until you create it.").classes("any-muted text-sm")
        request = ui.textarea(placeholder="Open the home page, search for 'shoes' and add a note.") \
            .props('outlined autogrow aria-label="What should the test do?" data-testid="ai-draft-input"') \
            .classes("w-full")
        with ui.element("div").classes("any-dialog-actions is-start"):
            go = ui.button("Draft", icon=icon("sparkles"), color=None).props('data-testid="ai-draft"') \
                .classes("any-primary")
        out = ui.element("div").classes("any-ai-body")

        async def draft() -> None:
            if not (request.value or "").strip():
                ui.notify("Describe the test first.", type="warning")
                return
            _busy(go, True)
            out.clear()
            pages: dict[str, dict[str, str]] = {}
            for name, rel in svc.pom.screen_index(system).items():
                try:
                    data, _ = svc.pom.load(system, rel)
                except (OSError, ValueError, PathError):
                    continue
                pages[name] = {f: str((c or {}).get("type", "text")) for f, c in (data.get("fields") or {}).items()
                               if isinstance(c, dict)}
            refs, _ = svc.tests.list_tests(system)
            example = None
            if refs:
                try:
                    example, _ = svc.tests.get(system, refs[0].file, refs[0].index)
                except (OSError, ValueError, PathError):
                    example = None
            try:
                result, usage = await run.io_bound(svc.ai.draft_test, system=system, request=request.value,
                                                   pages=pages, common_actions=list(svc.tests.common_actions()),
                                                   example=example)
            except AIError as e:
                _error(out, str(e))
                return
            finally:
                _busy(go, False)
            tc = draft_to_test_case(result, system, "dev")
            check = svc.tests.validate(system, tc)
            state["tc"] = tc
            with out:
                for msg, cls in [(m, "is-error") for m in check.errors] + [(m, "") for m in check.warnings]:
                    with ui.element("div").classes("any-callout is-inline").props('role="status"'):
                        ui.icon(icon("triangle-alert")).props('aria-hidden="true"')
                        text("div", msg, f"any-callout-text {cls}")
                for note in result.notes:
                    text("div", f"Note: {note}", "any-muted text-sm")
                text("pre", json.dumps(tc, indent=2, ensure_ascii=False), "any-pre any-cfg") \
                    .props('data-testid="ai-draft-json"')
                _usage_note(usage)
                with ui.element("div").classes("any-dialog-actions"):
                    create_btn = ui.button("Create test case...", icon=icon("save"), color=None, on_click=create) \
                        .props('data-testid="ai-draft-create"').classes("any-primary")
                    create_btn.set_enabled(not check.errors)

        def create() -> None:
            tc = state["tc"]
            if not tc:
                return
            dialog.close()

            def created(tid: str, rel: str) -> None:
                svc.ai.audit("accepted", {"feature": "test_draft", "test_id": tid, "file": rel})
                editor._create(rel, {**tc, "testCaseID": tid})

            editor._file_dialog("Create the drafted test case", tc["testCaseID"], created)

        go.on_click(draft)
    dialog.open()


# ───────────── 15: run summary ─────────────

def open_run_summary(run_obj) -> None:
    svc = services()
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[44rem]").props(
            'role="dialog" aria-label="Run summary"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div"):
                text("div", "AI summary", "any-eyebrow")
                text("h2", f"Run {run_obj.id}", "any-title mono")
            icon_button("x", "Close", dialog.close)
        ui.label("Written from the run's results (test names, outcomes and first error lines); logs are not sent.") \
            .classes("any-muted text-sm")
        out = ui.element("div").classes("any-ai-body")
        with out:
            ui.spinner(size="lg").props('aria-label="Summarizing"')

        async def summarize() -> None:
            try:
                result, usage = await run.io_bound(svc.ai.summarize_run, facts=run_facts(run_obj, svc.runs.list()))
            except AIError as e:
                out.clear()
                _error(out, str(e))
                return
            out.clear()
            plain = "\n\n".join([result.headline, *result.paragraphs,
                                 *(["Next:"] + [f"- {a}" for a in result.action_items] if result.action_items else [])])
            with out:
                text("h3", result.headline, "any-ai-headline").props('data-testid="ai-summary-headline"')
                for p in result.paragraphs:
                    text("p", p, "any-ai-summary")
                if result.action_items:
                    text("div", "Next", "any-eyebrow")
                    with ui.element("ul").classes("any-ai-list"):
                        for a in result.action_items:
                            text("li", a)
                with ui.element("div").classes("any-dialog-actions is-start"):
                    copy_button(plain, "Copy summary", "is-32")
                    text("span", "Copy as text", "any-muted text-sm")
                _usage_note(usage)

        ui.timer(0.05, summarize, once=True)
    dialog.open()
