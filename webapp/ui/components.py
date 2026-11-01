"""Small presentational building blocks for the Control page. Markup only: no business logic."""
from __future__ import annotations

import re
from typing import Callable

from nicegui import ui
from nicegui.elements.mixins.text_element import TextElement

from webapp.ui.theme import icon, svg

TEST_ID_RE = re.compile(r"^(TC-.+?-\d+)-(.+)$")

# Outcome -> visual tone (status tokens in automateny.css)
TONES = {
    "passed": "passed", "xpassed": "passed",
    "failed": "failed", "error": "failed",
    "running": "running",
}
QUEUED_TONE = "idle"  # queued / skipped / cancelled / not run / xfailed


def tone(outcome: str) -> str:
    return TONES.get(outcome, QUEUED_TONE)


def split_test_id(test_id: str) -> tuple[str, str]:
    """'TC-GOOGLE-001-HomePage' -> ('HomePage', 'TC-GOOGLE-001'); IDs that don't match stay whole."""
    m = TEST_ID_RE.match(test_id or "")
    return (m.group(2), m.group(1)) if m else (test_id, "")


def fmt_seconds(value: float) -> str:
    return f"{value:.2f} s"


def plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


class Label(TextElement):
    """A real <label for=...> element (ui.label renders a div)."""

    def __init__(self, text: str, for_id: str) -> None:
        super().__init__(tag="label", text=text)
        self._props["for"] = for_id


def eyebrow(text: str, *, rule: bool = True, tag: str = "div") -> ui.element:
    el = ui.element(tag).classes("any-eyebrow-row" if rule else "")
    with el:
        ui.label(text).classes("any-eyebrow")
    return el


def field(label: str, for_id: str, hint: str | None = None) -> ui.element:
    """Label above a control. Use as a context manager and build the control inside."""
    box = ui.element("div").classes("any-field")
    with box:
        if hint:
            with ui.element("div").classes("any-label-row"):
                Label(label, for_id).classes("any-label")
                ui.label(hint).classes("any-label-hint")
        else:
            Label(label, for_id).classes("any-label")
    return box


def icon_button(name: str, aria: str, on_click: Callable | None = None, size: str = "") -> ui.button:
    btn = ui.button(icon=icon(name), on_click=on_click, color=None) \
        .props(f'flat unelevated no-caps aria-label="{aria}"')
    return btn.classes(f"any-iconbtn {size}".strip()).tooltip(aria)


def copy_button(text: str, aria: str, size: str = "is-24") -> ui.button:
    def copy() -> None:
        ui.clipboard.write(text)
        ui.notify("Copied", type="positive", timeout=1200)
    return icon_button("copy", aria, copy, size)


def status_pill_template(value_expr: str = "props.value") -> str:
    """Vue template for a status pill inside a q-table slot (text-only interpolation)."""
    cls = ("{passed:'passed',xpassed:'passed',failed:'failed',error:'failed',running:'running'}"
           f"[{value_expr}] || 'idle'")
    return (f'<span :class="\'any-pill pill-\' + ({cls})"><span class="dot" aria-hidden="true"></span>'
            f'{{{{ {value_expr} }}}}</span>')


def test_case_template(muted_expr: str = "false") -> str:
    """Two-line name/code cell; row needs name, code and test_id fields."""
    return (f'<div :title="props.row.test_id" :aria-label="props.row.test_id">'
            f'<div :class="[\'any-tc-name\', ({muted_expr}) ? \'is-muted\' : \'\']">{{{{ props.row.name }}}}</div>'
            f'<div v-if="props.row.code" class="any-tc-code">{{{{ props.row.code }}}}</div></div>')


def pager_template(total: int) -> str:
    """Bottom slot for q-table: 'Showing a–b of n', rows per page, prev/next. Uses q-table's own pagination."""
    options = "".join(f'<option value="{n}">{n or "All"}</option>' for n in (5, 7, 10, 15, 20, 25, 50, 0))
    rpp = "props.pagination.rowsPerPage"
    page = "props.pagination.page"
    # q-table's own (filtered) row count; the server-side count until q-table is reachable
    qtable = "(($parent.$refs && $parent.$refs.qRef) || $parent)"
    n = f"({qtable}.computedRowsNumber ?? {total})"
    first = f"({n} ? (({rpp} ? ({page} - 1) * {rpp} : 0) + 1) : 0)"
    last = f"({rpp} ? Math.min({page} * {rpp}, {n}) : {n})"
    return f'''
      <div class="any-pager">
        <div>Showing <b>{{{{ {first} }}}}–{{{{ {last} }}}}</b> of <b>{{{{ {n} }}}}</b></div>
        <div class="any-pager-right">
          <label class="any-muted">Rows per page
            <select class="any-pager-select" :value="{rpp}"
                    @change="e => {qtable}.setPagination({{rowsPerPage: +e.target.value, page: 1}})">
              {options}
            </select>
          </label>
          <button type="button" class="any-pager-btn" aria-label="Previous page"
                  :disabled="props.isFirstPage" @click="props.prevPage">{svg("chevron-left")}</button>
          <button type="button" class="any-pager-btn" aria-label="Next page"
                  :disabled="props.isLastPage" @click="props.nextPage">{svg("chevron-right")}</button>
        </div>
      </div>'''


def styled_table(table: ui.table, total: int, wide: bool = False) -> ui.table:
    """Apply the shared table look and pagination row to a q-table."""
    table.props("flat square").classes("any-table" + (" is-wide" if wide else ""))
    table.add_slot("bottom", pager_template(total))
    return table


def text(tag: str, content: str, cls: str = "") -> TextElement:
    """A text-only element with any tag (span, b, code, h2, ...); content is always rendered as text."""
    el = TextElement(tag=tag, text=content)
    return el.classes(cls) if cls else el


RESULT_COLUMNS = [
    {"name": "outcome", "label": "Status", "field": "outcome", "align": "left", "sortable": True,
     "style": "width: 140px", "headerStyle": "width: 140px"},
    {"name": "test_id", "label": "Test case", "field": "test_id", "align": "left", "sortable": True},
    {"name": "browser", "label": "Browser", "field": "browser", "align": "left"},
    {"name": "duration", "label": "Duration", "field": "duration", "align": "right", "sortable": True},
    {"name": "recent", "label": "Recent", "field": "recent", "align": "left"},
    {"name": "message", "label": "Message", "field": "message", "align": "left"},
]


def result_slots(table: ui.table) -> None:
    """Cells for a per-test results table (rows from test_details.result_rows)."""
    table.add_slot("body-cell-outcome", f'<q-td :props="props">{status_pill_template()}</q-td>')
    table.add_slot("body-cell-test_id", f'<q-td :props="props">{test_case_template()}</q-td>')
    table.add_slot("body-cell-browser", f'''
        <q-td :props="props">
          <span class="any-browser">{svg("globe")}{{{{ props.value || '—' }}}}</span>
        </q-td>''')
    table.add_slot("body-cell-duration", '''
        <q-td :props="props" class="any-num">{{
          props.row.outcome === 'running' || !props.value ? '—' : props.value.toFixed(2) + ' s'
        }}</q-td>''')
    table.add_slot("body-cell-message", f'''
        <q-td :props="props">
          <div class="any-msg-cell">
            <div class="flex flex-col gap-1 min-w-0">
              <span v-if="props.row.where" class="any-where"
                    title="Where the test failed: screen, field (action)">{{{{ props.row.where }}}}</span>
              <span v-if="props.value"
                    :class="['any-msg', ['failed', 'error'].includes(props.row.outcome) ? 'is-fail' : '']"
              >{{{{ props.value }}}}</span>
              <span v-else class="any-msg">—</span>
            </div>
            <span v-if="props.row.has_details" class="any-details-chip"
                  title="Click the row for failure details">{svg("file-text")}Details</span>
          </div>
        </q-td>''')
    # Last results of the test on this system/env, oldest first (see run_insights.trends)
    table.add_slot("body-cell-recent", '''
        <q-td :props="props">
          <div class="any-recent">
            <span v-if="props.value && props.value.length" class="any-dots" role="img"
                  :aria-label="'Last ' + props.value.length + ' results, oldest first: ' + props.value.join(', ')">
              <i v-for="(o, i) in props.value" :key="i" :title="o"
                 :class="{'is-pass': ['passed', 'xfailed'].includes(o), 'is-fail': ['failed', 'error'].includes(o)}"
              ></i>
            </span>
            <span v-else class="any-muted">—</span>
            <span v-if="props.row.flaky" class="any-flaky-chip"
                  title="Passed and failed in recent runs, and changed its result at least twice">Flaky</span>
          </div>
        </q-td>''')
    table.classes("is-clickable")


def placeholder(select: ui.select, hint: str) -> ui.select:
    """Placeholder text for an empty select (Quasar only shows `placeholder` on selects with text input)."""
    select.style(f'--any-ph: "{hint}"')

    def sync() -> None:
        if select.value in (None, "", []):
            select.classes(add="is-empty")
        else:
            select.classes(remove="is-empty")

    select.on_value_change(lambda _: sync())
    sync()
    return select
