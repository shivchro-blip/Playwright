"""Ask before a big run: how many test runs, where, and about how long."""
from __future__ import annotations

from nicegui import ui

from webapp.services.run_estimate import estimate, format_duration
from webapp.ui.components import plural, text


async def confirm_big_run(*, tests: int, browsers: int, system: str, env: str, groups: list[str],
                          runs_so_far, test_ids: list[str]) -> bool:
    """True when the person goes ahead. `groups`: the folders the tests come from."""
    total = tests * browsers
    guess = estimate(runs_so_far, system, env, test_ids)
    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog").props('data-testid="run-confirm"'):
        text("h2", f"Run {total} test {plural(total, 'run')}?", "any-title")
        ui.label(f"{tests} {plural(tests, 'test')} x {browsers} {plural(browsers, 'browser')} on {system} / {env}"
                 + (f", from {', '.join(groups[:6])}{' ...' if len(groups) > 6 else ''}" if groups else "")
                 + ".").classes("any-dialog-text").props('data-testid="run-confirm-what"')
        if guess:
            seconds, known, count = guess
            ui.label(f"About {format_duration(seconds * browsers)} one after the other, from earlier runs of "
                     f"{known} of these {plural(count, 'test')}.").classes("any-muted text-sm")
        ui.label("To run fewer, untick tests in the table or pick one folder in the folder filter.") \
            .classes("any-muted text-sm")
        with ui.element("div").classes("any-dialog-actions"):
            ui.button("Cancel", color=None, on_click=lambda: dialog.submit(False)).classes("any-btn is-38") \
                .props('data-testid="run-confirm-cancel"')
            ui.button(f"Run {total}", color=None, on_click=lambda: dialog.submit(True)).classes("any-primary") \
                .props('data-testid="run-confirm-go"')
    result = await dialog
    dialog.delete()
    return bool(result)
