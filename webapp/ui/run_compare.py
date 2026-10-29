"""Compare two runs test by test: new failures, fixes, still failing, slower/faster, added and removed tests."""
from __future__ import annotations

from nicegui import ui

from webapp.services.run_insights import CHANGES, compare, finished, previous_run, summarize
from webapp.state import services
from webapp.ui.components import field, icon_button, split_test_id, status_pill_template, styled_table, text

TONES = {"new failure": "failed", "still failing": "failed", "fixed": "passed", "faster": "passed",
         "slower": "idle", "status changed": "idle", "added": "running", "removed": "idle", "unchanged": "idle"}


def _label(run) -> str:
    return f"{run.id} · {run.status} · {(run.started_at or run.created_at)[:16].replace('T', ' ')}"


def open_compare(run_id: str) -> None:
    """Compare `run_id` (after) with an earlier run of the same system/env (before, default: the previous one)."""
    svc = services()
    target = svc.runs.get(run_id)
    if not target:
        ui.notify("Run not found.", type="negative")
        return
    runs = svc.runs.list()
    candidates = [r for r in reversed(finished(runs, target.config.system)) if r.id != target.id]
    base = previous_run(runs, target) or (candidates[0] if candidates else None)
    state = {"show_unchanged": False}

    with ui.dialog() as dialog, ui.card().classes("any-app any-dialog any-details").props(
            'role="dialog" aria-label="Compare runs"'):
        with ui.element("div").classes("any-details-head"):
            with ui.element("div").classes("min-w-0"):
                text("div", "Compare runs", "any-eyebrow")
                with ui.element("div").classes("any-run-title"):
                    text("h2", target.id, "any-title any-details-name mono")
                    text("span", f"{target.config.system} / {target.config.env}", "any-tc-code")
            icon_button("x", "Close", dialog.close)
        if not candidates:
            ui.label(f"No other finished run of {target.config.system} to compare with.").classes("any-muted")
            dialog.open()
            return
        with ui.element("div").classes("any-form-row"):
            with field("Compare with (before)", "cmp-base").classes("is-grow"):
                select = ui.select({r.id: _label(r) for r in candidates}, value=base.id if base else None,
                                   on_change=lambda: body.refresh()).props('for="cmp-base"').classes("w-full")
            ui.switch("Show unchanged tests", on_change=lambda e: (state.update(show_unchanged=e.value),
                                                                    body.refresh()))

        @ui.refreshable
        def body() -> None:
            before = svc.runs.get(select.value) if select.value else None
            if not before:
                return
            # Always earlier -> later, whichever way round they were picked
            first, second = sorted((before, target), key=lambda r: r.created_at)
            rows = compare(first, second)
            counts = summarize(rows)
            with ui.element("div").classes("any-lc-summary").props('data-testid="cmp-summary"'):
                text("span", f"{first.id} → {second.id}", "any-lc-desc")
                for change in CHANGES:
                    if counts.get(change):
                        with ui.element("span").classes(f"any-pill pill-{TONES[change]}"):
                            ui.element("span").classes("dot").props('aria-hidden="true"')
                            text("span", f"{counts[change]} {change}")
            shown = [r for r in rows if state["show_unchanged"] or r.change != "unchanged"]
            if not shown:
                ui.label("No differences: every test has the same result and similar duration.").classes("any-muted")
                return
            table_rows = []
            for r in shown:
                name, code = split_test_id(r.test_id)
                table_rows.append({"key": f"{r.test_id}/{r.browser}", "change": r.change, "test_id": r.test_id,
                                   "name": name, "code": code, "browser": r.browser, "before": r.before or "—",
                                   "after": r.after or "—", "tone": TONES[r.change],
                                   "before_s": "—" if r.before_s is None else f"{r.before_s:.2f} s",
                                   "after_s": "—" if r.after_s is None else f"{r.after_s:.2f} s"})
            with ui.element("div").classes("any-table-wrap"):
                table = ui.table(rows=table_rows, row_key="key", pagination=15, columns=[
                    {"name": "change", "label": "Change", "field": "change", "align": "left"},
                    {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
                    {"name": "browser", "label": "Browser", "field": "browser", "align": "left"},
                    {"name": "before", "label": "Before", "field": "before", "align": "left"},
                    {"name": "after", "label": "After", "field": "after", "align": "left"},
                    {"name": "before_s", "label": "Before (s)", "field": "before_s", "align": "right"},
                    {"name": "after_s", "label": "After (s)", "field": "after_s", "align": "right"},
                ]).classes("w-full is-compact")
                styled_table(table, len(table_rows), wide=True)
                table.add_slot("body-cell-change", '''
                    <q-td :props="props"><span :class="'any-pill pill-' + props.row.tone">
                      <span class="dot" aria-hidden="true"></span>{{ props.value }}</span></q-td>''')
                table.add_slot("body-cell-test_id", '''
                    <q-td :props="props"><div class="any-tc-name">{{ props.row.name }}</div>
                      <div v-if="props.row.code" class="any-tc-code">{{ props.row.code }}</div></q-td>''')
                for col in ("before", "after"):
                    table.add_slot(f"body-cell-{col}", f'<q-td :props="props">{status_pill_template()}</q-td>')
                for col in ("before_s", "after_s"):
                    table.add_slot(f"body-cell-{col}", '<q-td :props="props" class="any-num">{{ props.value }}</q-td>')

        body()
    dialog.open()
