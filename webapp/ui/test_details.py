"""Failure details for one test of a run: error, traceback, screenshots, DOM snapshot, trace, video, log.

Opened from the results tables (Control and History). Everything shown comes from the run's own files;
attachment text is rendered as text, and HTML snapshots open in a new tab that the app serves sandboxed.
"""
from __future__ import annotations

from nicegui import ui

from webapp.services.run_artifacts import TestDetails
from webapp.services.run_insights import trends
from webapp.services.run_manager import Run
from webapp.state import services
from webapp.ui.ai_panels import ai_enabled, triage_section
from webapp.ui.components import fmt_seconds, icon_button, split_test_id, text, tone
from webapp.ui.console_view import ConsoleView
from webapp.ui.theme import icon

LOG_LINES = 1500


def details_for(run: Run) -> dict[tuple[str, str], TestDetails]:
    return services().artifacts.details(run.id, run.results_dir)


def has_details(details: dict[tuple[str, str], TestDetails], test_id: str, browser: str) -> bool:
    item = details.get((test_id, browser))
    return bool(item and (item.has_artifacts or item.log))


def result_rows(run: Run) -> list[dict]:
    """Rows for a per-test results table (see components.result_slots), with each test's recent results."""
    details = details_for(run)
    recent = trends(services().runs.list(), run.config.system, run.config.env, until=run.created_at)
    rows = []
    for r in list(run.results.values()):
        name, code = split_test_id(r.test_id)
        trend = recent.get((r.test_id, r.browser))
        rows.append({"nodeid": r.nodeid, "outcome": r.outcome, "test_id": r.test_id, "name": name, "code": code,
                     "browser": r.browser, "duration": r.duration, "message": r.message,
                     "where": details[(r.test_id, r.browser)].failure.short
                     if (r.test_id, r.browser) in details and details[(r.test_id, r.browser)].failure else "",
                     "has_details": has_details(details, r.test_id, r.browser),
                     "recent": trend.outcomes if trend else [], "flaky": bool(trend and trend.flaky)})
    return rows


def _link(label: str, url: str, icon_name: str, download: bool = False) -> None:
    props = f'href="{url}" target="_blank" rel="noopener"' + (" download" if download else "")
    with ui.element("a").classes("any-artifact-link").props(props):
        ui.icon(icon(icon_name)).props('aria-hidden="true"')
        text("span", label)


def open_test_details(run: Run, row: dict) -> None:
    """row: a results-table row (test_id, browser, outcome, duration, message)."""
    test_id, browser = str(row.get("test_id") or ""), str(row.get("browser") or "")
    item = details_for(run).get((test_id, browser))
    name, code = split_test_id(test_id)
    outcome = str(row.get("outcome") or (item.status if item else ""))

    with ui.dialog().props("maximized=false") as dialog, \
            ui.card().classes("any-app any-dialog any-details").props('role="dialog" aria-label="Test details"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Test details", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", name, "any-title any-details-name")
                    with ui.element("span").classes(f"any-pill pill-{tone(outcome)}"):
                        ui.element("span").classes("dot").props('aria-hidden="true"')
                        text("span", outcome or "unknown")
                with ui.element("div").classes("any-meta"):
                    if code:
                        text("span", code, "v")
                    if browser:
                        text("span", browser, "v")
                    duration = row.get("duration")
                    if duration:
                        text("span", fmt_seconds(float(duration)), "v")
                    text("span", f"Run {run.id}", "v")
            icon_button("x", "Close", dialog.close)

        point = item.failure if item else None
        if point:
            with ui.element("section").classes("any-failat").props('data-testid="failed-at"'):
                with ui.element("div").classes("min-w-0 flex flex-col gap-2"):
                    text("div", "Failed at", "any-eyebrow")
                    with ui.element("ol").classes("any-crumbs"):
                        for crumb in point.crumbs:
                            text("li", crumb)
                shot = point.screenshot
                if shot:
                    with ui.element("a").props(f'href="{shot.url}" target="_blank" rel="noopener" '
                                               'aria-label="Open the failure screenshot"'):
                        ui.element("img").props(f'src="{shot.url}" alt="{shot.name}" loading="lazy"') \
                            .classes("any-failat-shot")
        message = (item.message if item else "") or str(row.get("message") or "")
        if message:
            with ui.element("section").classes("any-error-box"):
                text("div", "Error", "any-eyebrow")
                text("pre", message, "any-pre is-error")
        if ai_enabled() and outcome in ("failed", "error", "broken"):
            triage_section(run, {**row, "test_id": test_id, "browser": browser, "outcome": outcome}, item)
        if item and item.trace:
            with ui.expansion("Traceback", icon=icon("terminal")).classes("any-disclosure"):
                text("pre", item.trace, "any-pre")

        images = item.by_kind("image") if item else []
        if images:
            with ui.element("section").classes("any-shots"):
                text("div", "Screenshots", "any-eyebrow")
                with ui.element("div").classes("any-shot-grid"):
                    for shot in images:
                        with ui.element("figure").classes("any-shot"):
                            with ui.element("a").props(f'href="{shot.url}" target="_blank" rel="noopener" '
                                                       f'aria-label="Open screenshot {shot.name}"'):
                                ui.element("img").props(f'src="{shot.url}" alt="{shot.name}" loading="lazy"')
                            text("figcaption", shot.name)

        videos = item.by_kind("video") if item else []
        for video in videos:
            with ui.element("section").classes("any-shots"):
                text("div", video.name or "Video", "any-eyebrow")
                ui.element("video").props(f'src="{video.url}" controls preload="metadata"').classes("any-video")

        links = [(a, "file-text", False) for a in (item.by_kind("html") if item else [])]
        links += [(a, "save", True) for a in (item.by_kind("trace") if item else [])]
        links += [(a, "file-text", False) for a in (item.by_kind("other") if item else [])]
        if links:
            with ui.element("section").classes("any-artifacts"):
                text("div", "Files", "any-eyebrow")
                with ui.element("div").classes("any-artifact-row"):
                    for att, ico, download in links:
                        label = ("Playwright trace (.zip)" if att.kind == "trace"
                                 else "DOM snapshot" if att.kind == "html" and att.name == "DOM Snapshot"
                                 else att.name or att.kind)
                        _link(label, att.url, ico, download)
                if item and item.by_kind("trace"):
                    ui.label("Open a trace with `playwright show-trace <file>`, or drop it on trace.playwright.dev "
                             "(the file is processed in your browser, not uploaded).").classes("any-muted text-xs")

        texts = item.by_kind("text") if item else []
        for att in texts:
            with ui.expansion(att.name, icon=icon("braces")).classes("any-disclosure"):
                text("pre", att.text or "", "any-pre")

        if item and item.log:
            with ui.expansion("Test log", icon=icon("terminal")).classes("any-disclosure is-console"):
                with ui.element("div").classes("any-console is-inline"):
                    ConsoleView(max_lines=LOG_LINES, lines=item.log.splitlines()[-LOG_LINES:])

        if not (message or item and (item.attachments or item.log)):
            ui.label("No failure details were recorded for this test.").classes("any-muted")
    dialog.open()
