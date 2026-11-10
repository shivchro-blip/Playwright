"""File a bug for a failed test in the configured issue tracker (GitHub Issues or Jira), written up for you."""
from __future__ import annotations

from nicegui import run as nrun
from nicegui import ui

from webapp.auth import audit, guard
from webapp.services.issue_tracker import IssueError, build_issue
from webapp.state import services
from webapp.ui.components import text
from webapp.ui.theme import icon


def issue_button(run, result_row: dict, item) -> None:
    """The 'File a bug' button of the test details dialog (only when a tracker is set up and the test failed)."""
    svc = services()
    if svc.issues.blocker() or str(result_row.get("outcome")) not in ("failed", "error"):
        return
    ui.button("File a bug", icon=icon("external-link"), color=None,
              on_click=lambda: open_issue_dialog(run, result_row, item)) \
        .props('flat unelevated no-caps data-testid="file-bug"').classes("any-btn is-38")


async def open_issue_dialog(run, result_row: dict, item) -> None:
    svc = services()
    if not guard("edit", "file a bug", str(result_row.get("test_id"))):
        return
    result = next((r for r in run.results.values()
                   if r.test_id == result_row.get("test_id") and r.browser == result_row.get("browser")), None)
    if result is None:
        ui.notify("That result is no longer in the run.", type="warning")
        return
    kind = svc.issues.kind
    issue = build_issue(run, result, item, svc.settings.resolved_public_url)
    earlier = svc.issues.existing(run, result.test_id, result.browser)
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[44rem]").props(
            'role="dialog" aria-label="File a bug"'):
        text("h2", "File a bug" + (" in Jira" if kind == "jira" else " on GitHub"), "any-title")
        if earlier:
            with ui.element("div").classes("any-note").props('data-testid="bug-existing"'):
                ui.label("A bug was already filed for this failure: ")
                with ui.element("a").props(f'href="{earlier.url}" target="_blank" rel="noopener"'):
                    ui.label(earlier.key)
        ui.label("Check the text, then File. The error, the failing step and the run report link are included; "
                 "nothing else leaves this app.").classes("any-muted text-sm")
        title = ui.input(value=issue.title).props('aria-label="Title" data-testid="bug-title"').classes("w-full")
        body = ui.textarea(value=issue.wiki if kind == "jira" else issue.markdown) \
            .props('aria-label="Description" rows=14 data-testid="bug-body"').classes("w-full mono-input")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=dialog.close).classes("any-btn is-38")
            go = ui.button("File another" if earlier else "File the bug", color=None) \
                .props('data-testid="bug-file"').classes("any-primary")

        async def file() -> None:
            go.props("loading")
            try:
                final = type(issue)(title.value.strip() or issue.title, body.value, body.value)
                filed = await nrun.io_bound(svc.issues.file, run, result.test_id, result.browser, final)
            except IssueError as e:
                ui.notify(str(e), type="negative", multi_line=True, timeout=10000)
                return
            finally:
                go.props(remove="loading")
            audit("issue.create", f"{result.test_id}", run=run.id, tracker=filed.tracker, key=filed.key)
            dialog.close()
            ui.notify(f"Filed {filed.key}: {filed.url}", type="positive", multi_line=True, timeout=12000)

        go.on_click(file)
    dialog.open()
