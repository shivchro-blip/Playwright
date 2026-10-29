# Control page: element inventory (acceptance checklist)

## Stack (confirmed from the source)

There is no `package.json` and no separate frontend. The UI is **NiceGUI 3.18** (Python). It renders **Vue 3 + Quasar 2** components that are driven from the server over a socket.io websocket.

- **Components:** Quasar (`q-select`, `q-table`, `q-btn`, …) wrapped by `nicegui.ui.*`.
- **Styling:** Quasar classes, Tailwind utility classes (NiceGUI ships them), and `ui.add_css`.
- **Icons:** Material Icons (bundled with NiceGUI).
- **Build tool:** none. Python serves the static assets.
- **Interface between UI and server:** UI events are handled in Python (`webapp/ui/control.py`), which calls the services directly. The Control page makes **no REST calls**. The `/api/*` router is for scripts and CI. The "payload" of a run is therefore the `RunConfig` passed to `RunManager.start()`, which is persisted to `runs/<id>/run.json`, plus the pytest command line.

How the spec maps onto this stack:

| Spec | Implementation |
|---|---|
| Theme (MUI/Vuetify) | CSS custom properties plus scoped overrides of Quasar classes (`webapp/static/automateny.css`) |
| @fontsource | woff2 files copied from `@fontsource/ibm-plex-sans` / `ibm-plex-mono` 5.3.0 into `webapp/static/fonts/`, served by the app |
| lucide-react / lucide-vue | Lucide 1.63.0 SVGs compiled into a sprite (`webapp/static/icons.svg`), used through Quasar's `svguse:` icon names |

## Elements

"Handler" means a method of `ControlPanel` (`webapp/ui/control.py`) unless stated otherwise.

**Status** records where each element lives after the redesign: ✅ kept, with its new location.

### App bar (`webapp/ui/shell.py`)

| Label | Component | Handler / action | API | Enabled when | Visible when | Status |
|---|---|---|---|---|---|---|
| Control / Generator / History / Help tabs | `ui.tabs` + `ui.tab`, linked to `ui.tab_panels` (same page, no routing) | client-side tab switch | – | always | always | ✅ dark app bar, Lucide icons, `aria-current` on the active tab |
| API | `ui.link("/docs", new_tab)` | opens FastAPI docs | `/docs` | always | always | ✅ right-aligned, external-link icon |
| `watch(run_id)` (Generator "Run" / History "Re-run" jump here) | shell callback → `ControlPanel.view_run` | switches to Control and follows the run | – | – | – | ✅ unchanged |

### Run setup

| Label | Component | Handler / action | Service | Enabled when | Visible when | Status |
|---|---|---|---|---|---|---|
| System | `ui.select(self.systems)`; initial value = `.env systemConfig`, else the first one | `on_system_change`: reset environments, `reload_catalog`, refresh credentials | `systems.environments`, `catalog.load` | always | always | ✅ Sidebar › Target |
| Environment | `ui.select(envs)` | refresh credentials | `credential_status` | always | always | ✅ Sidebar › Target |
| Tags | `ui.select(multiple, with_input, new_value_mode=add-unique, clearable, use-chips)` | `tests_view.refresh` | – | always | always | ✅ Sidebar › Target. Chips have a remove button; there is a clear-all button |
| Tags: chip × | Quasar chip remove | removes that tag | – | always | per chip | ✅ `aria-label="Remove tag X"` |
| Tags: clear all | Quasar `clearable` | clears the tags | – | always | while it has tags | ✅ circle-x icon |
| Tags: add a new value | `new_value_mode=add-unique` (type and press Enter) | adds a free-text tag | – | always | always | ✅ unchanged |
| Browsers | `ui.select(BROWSERS + .env extras, multiple, use-chips)` | `tests_view.refresh` | – | always | always | ✅ Sidebar › Execution, globe chips |
| Parallel workers (pytest-xdist) | `ui.number(min=1, max=8, precision=0)` | read at Run → `RunConfig.workers` | – | always | always | ✅ stepper; the input still accepts typing, min 1 / max 8 |
| Browser & artifacts | `ui.expansion` | toggle | – | always | always | ✅ bordered disclosure with `aria-expanded` |
| ↳ Headless | `ui.switch` (`.env headlessMode`) | `_options` | – | always | expanded | ✅ |
| ↳ Maximized window | `ui.switch` | `_options` | – | always | expanded | ✅ |
| ↳ Slow-mo (ms) | `ui.number(min=0, step=50)` | `_options` | – | always | expanded | ✅ |
| ↳ Page wait until | `ui.select(load, domcontentloaded, networkidle, commit)` | `_options` | – | always | expanded | ✅ |
| ↳ Record video / Playwright trace / Snapshots | `ui.switch` ×3 | `_options` | – | always | expanded | ✅ |
| Login credentials: no systemData entry | label "No login entry in systemData.json for this system/environment." (orange) | – | `credential_status` = None | – | status is None | ✅ Sidebar › Access (warning colour) |
| Login credentials: none needed | label "No credentials needed." | – | status = [] | – | status is [] | ✅ |
| Login credentials: per key | icon + key (mono) + "set" / "missing - add it to .env" | – | status list | – | for each required key | ✅ |
| Run | `ui.button` | `start_run`: checks that tags select tests (else notify), checks credentials (notifies), then `runs.start(RunConfig)` | `RunManager.start` | no run is active (`_update_buttons`) | always | ✅ **moved** to the launch strip in the Tests card (`data-testid=run-button`). Also disabled when R = 0 |
| Cancel | `ui.button` (negative, outline) | `cancel_run` → `runs.cancel` | `RunManager.cancel` | the viewed run is active | always (disabled otherwise) | ✅ **moved** to the Current run header (`data-testid=cancel-run`). Only shown while active, because it does nothing otherwise |
| Save as defaults | `ui.button` (flat) | `save_defaults` → `env.save(...)` | `EnvConfigService.save` | always | always | ✅ sidebar footer |
| .env path | label `.env: <path>` | – | `env.env_path` | – | always | ✅ sidebar footer |

### Tests

| Label | Component | Handler / action | Enabled when | Visible when | Status |
|---|---|---|---|---|---|
| "Selected only" | `ui.switch(value=True)` | `_set_selected_only` → `tests_view.refresh` | always | always | ✅ card header, `role=switch` |
| Reload | `ui.button(icon=refresh)`, tooltip "Reload test files" | `reload_catalog` | always | always | ✅ `aria-label="Reload tests"` |
| "X of Y test case(s) selected × B browser(s) = R run(s)" | label | computed from `catalog.select(tags)` and the browser count (`max(len(browsers), 1)`) | – | always | ✅ split into the header pill "X of Y selected" and the launch-strip formula (`data-testid=run-formula`) |
| "Could not read <file>: <err>" | label (red), one per catalog error | – | – | when there are errors | ✅ under the card header |
| "Duplicate test IDs: …" | label (orange) | – | – | when there are duplicates | ✅ under the card header |
| Selection column | ✓ / – icon, **read-only**. Selection is decided by the Tags filter; rows can't be toggled | – | – | always | ✅ read-only checkbox (see deviations) |
| Table columns | Test case (sortable), Tags, File (sortable), Steps (sortable) | Quasar client-side sort | – | – | ✅ sorting kept |
| Pagination | Quasar, 10 rows/page, default "Records per page" options [5, 7, 10, 15, 20, 25, 50, All] | Quasar | – | always | ✅ custom row, same options and state |

### Current run

| Label | Component | Source | Enabled when | Visible when | Status |
|---|---|---|---|---|---|
| Card | `ui.card`, always rendered; before any run the title is "-" and the badge says "idle" | – | – | always | ✅ always rendered; idle empty state |
| Run title | `"{id} · {system}/{env} · {tags}"` | `tick` | – | once a run is viewed | ✅ labelled meta row, copy-ID button |
| Status badge | `ui.badge(run.status)`, colours from STATUS_COLORS | `tick` | – | always ("idle" before any run) | ✅ status tile + headline |
| Allure report | `ui.button` → `open_report`: opens `/runs/<id>/allure-report/index.html` if ready, else notifies with the message | `report_status == "ready"` | – | always | ✅ header (`data-testid=allure-report`) |
| Console log | `ui.button` → `open_console`: opens `/runs/<id>/console.log` | – | a run is viewed | always | ✅ **moved** to the console header (`data-testid=console-log`) |
| Progress | `ui.linear_progress`: done/expected, 1.0 when finished | `tick` | – | always | ✅ replaced by the duration bar (`data-testid=duration-bar`) |
| Summary | `"{done}/{expected} done · passed N · …"` (every outcome count); "Starting..." / "No test results recorded." | `tick` | – | always | ✅ summary row and legend; every non-zero outcome is listed |
| Message | orange label: `run.error`, or the report message when `report_status` is unavailable or failed | `tick` | – | when non-empty | ✅ warning callout (Allure-message parser with a raw fallback) |
| Results table | Status badge, Test case, Browser, Seconds, Message (truncated); sortable; 10 rows/page | `tick` | – | always | ✅ restyled; sorting and pagination kept; messages wrap |
| Live console | `ui.log(max_lines=1500)`; up to 400 new lines per 0.5 s tick; stays pinned to the bottom while the user is at the bottom | `tick` | – | always | ✅ custom `ConsoleView` (text nodes only; same buffering and pinning) |
| Finish notification | `ui.notify("Run X finished: status")` | `tick` | – | on active → finished | ✅ unchanged |
| Start / cancel / error notifications | `ui.notify` | `start_run`, `cancel_run`, `save_defaults` | – | – | ✅ unchanged |

### Run history (on the Control page; not in the redesign spec)

| Label | Component | Handler | Status |
|---|---|---|---|
| Run history card | `ui.table` of the last 25 runs, 5 rows/page; Started, Status, System, Env, Tags, Browsers, Result | `history`, refreshed on start and finish | ✅ kept, restyled with the shared table tokens |
| Row click | `rowClick` → `view_run(id)` | | ✅ |

## Status values

| Value | Produced by | Applies to |
|---|---|---|
| `queued` | `Run.status` default, set in `RunManager.start` | run |
| `running` | `_execute` (run); `_handle_event` on a "start" event (test) | run, test |
| `passed` / `failed` | `_final_status` (exit code 0 / 1); pytest report outcome | run, test |
| `error` | `_final_status` (spawn failure, no tests collected, other exit codes); test failed outside the call phase; a test still "running" when the run ends (not cancelled); history loaded mid-run | run, test |
| `cancelled` | `_final_status` when cancelled; tests still running at that moment | run, test |
| `skipped`, `xfailed`, `xpassed` | pytest report outcome (`pytest_events.py`) | test |
| `idle` | UI-only placeholder before any run is viewed | UI |
| "queued" / "not run" (display only) | the redesign shows `expected − reported` placeholders: "queued" while the run is active, "not run" after it ends | UI |

Report status: `""`, `generating`, `ready`, `unavailable`, `failed`.

## Verification record

Environment: NiceGUI 3.18 / FastAPI 0.162 / Playwright 1.64.1, headless Chromium. Runs used a temporary **LOCAL** system: a scratch workspace with a local HTTP page and 4 test cases. It is not committed. google.com is not reachable from the build environment, so a real GOOGLE/dev run could only fail on network access.

**Checks**

- **Unit tests:** `python -m pytest tests/unit` → 56 passed.
- **Lint:** `ruff check --select E,F,W --line-length 120 webapp/` → clean. This also clears the 4 long lines that existed before in `generator.py` and `history.py`.
- **Interaction pass:** 22 of 22 checks passed, with no browser console errors or warnings and no server tracebacks. Covered:
  - Control: worker stepper limits, chip remove and re-add, R = 0 disables Run and shows the helper text, disclosure `aria-expanded`, Save as defaults writes `.env`.
  - Generator: edit → Save writes the JSON file, Check, Form ↔ JSON, the New / Delete / field dialogs, the page editor.
  - History: search filter and pager count, row selection and details, delete confirm, console.
  - Help: topic switch and search.
- **Run payload:** the UI-started run's `RunConfig` and `expected`, as saved in `run.json`, are identical to the baseline captured with the old UI. The pytest command line built for that config is identical too. `git diff main -- webapp/services webapp/api.py utils tests/dynamic_test_suite.py` is empty, so no backend code changed.
- **Run states:** idle, running, passed, failed and cancelled, captured at 1440×900 and 390×844 (`screenshots/control-*.png`). There are also 1024 and 768 captures, and no page-level horizontal overflow at 1024, 768 or 390 on any tab.
- **Focus:** keyboard Tab through the Control page reaches every control, and each one shows a 2px ring (`screenshots/focus-run-button.png`).
- **Contrast:** every text/background token pair is at least 4.5:1. The lowest is muted text on chips, at 5.09.

## Other pages (in scope after the brief was extended)

| Page | Kept 1:1 | Now |
|---|---|---|
| Generator | System switch with the unsaved-changes guard; test/page lists with search, open, new and reload; editor with Form/JSON, Check, Revert, Copy, Delete, Run, Save; step add, move, duplicate and remove; form rows; page settings; field grid; field-keys dialog; every confirm | List and editor cards, label-above fields, step cards with kind tags, field grid, token dialogs, Lucide icons |
| History | System/status/search filters, single selection plus row click, 2 s auto-refresh, Watch live, Re-run, Allure report, Console log, Delete with confirm, last-300-lines console | Runs card with labelled filters and a run count, run-details card with status tile and fact grid, shared results table, dark console |
| Help | Topic list grouped by category, search with snippets, Markdown article | Topic nav uses real `<button>`s (keyboard-reachable; previously clickable divs), token article typography |

## Deviations from the brief

1. **No package.json / React / MUI.** The stack is NiceGUI (Quasar). The theme is CSS tokens plus `ui.colors` plus NiceGUI `default_props`. Fonts and icons are vendored from the npm packages (`@fontsource/ibm-plex-*` 5.3.0, OFL; `lucide-static` 1.63.0, ISC) into `webapp/static/`.
2. **Tests selection checkboxes are read-only.** Selection is decided by the Tags filter: the code has no per-row selection model, and adding one would change the run payload (`test_ids`). The checkboxes show the state, are inert (`aria-disabled`), and say so in a tooltip.
3. **"All tags" placeholder → "Select tags".** An empty tag list runs nothing, so "All tags" would be wrong.
4. **No REST calls to compare.** The UI talks to Python over the NiceGUI websocket. The run payload (RunConfig plus command) was compared instead.
5. **Cancel is hidden while no run is active.** `cancel_run` does nothing in that case.
6. **Focus ring.** Quasar's `q-btn` carries a cascade-layered `outline: 0 !important`, so buttons and switches draw the same 2px ring with a 2px offset as a box-shadow. On the dark app bar and console the ring uses `--accent-light`, because `--accent` would not be visible there.
7. **Page padding** is `clamp(16px, 4vw, 24px)`, so it is 16px at phone widths instead of a fixed 24px.
8. **Status words.** The run-status headline also covers `Error` and `Queued`, and the legend also lists `error`, `queued`, `cancelled`, `xfailed` and `xpassed` when they are greater than 0, so no count shown today is lost.
