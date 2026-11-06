# Control Tab

## Run setup

| Control | What it does |
|---|---|
| **System** | Folder under `Tests/` and `Pages/`, and the entry in `systemData.json`. |
| **Environment** | Environment key under that system in `systemData.json` (base URL, login). A test case's own `"env"` wins over this. |
| **Tags** | Only test cases with at least one matching tag run (case-insensitive). No tags = nothing runs. Type to add a tag that is not in the list. |
| **Browsers** | `chrome`, `msedge`, `chromium`, `firefox`, `webkit`. Each selected test runs once per browser. |
| **Parallel workers** | More than 1 runs tests in parallel with pytest-xdist. Live framework prints are hidden in parallel mode; results still update. |
| **Retry failed tests** | Run a test that failed again, once or twice (pytest-rerunfailures). The retry is **traced** even when *Playwright trace* is off, so the attempt that shows the problem has a trace. A test that passes only on a retry is reported as *Passed on retry* and counts as **flaky** in History. |
| **Also run on** | Pick one or more other environments of the system to run the same tests there too, as separate runs started together. When they finish, **Compare** in History opens with the other environment already chosen: *new failure* means a test fails on the later environment that passed on the earlier one. (REST API: `"also_envs": ["test"]`.) |
| **Run on** | Shown once remote runners exist (see *Remote runners*): this machine, one runner, or any runner with a label. |
| **Console log level** | What the run prints: *Errors only*, *Warnings and errors*, *Info* (default) or *Debug*. It sets pytest's console level and the framework loggers together (`LOG_LEVEL`, `TestSuiteLoggerLevel`, `PageLoggerLevel`, `FormHandlerLoggerLevel`), so it takes precedence over per-module levels in `.env` for runs started here. Result lines (PASSED/FAILED) always show. *Save as defaults* keeps the choice. |

**Browser & artifacts**: headless, maximized window, slow-mo, page wait condition, video recording, Playwright trace, snapshots, the accessibility scan and self-healing locators (see *Self-healing locators*). These apply to this run only.

**Accessibility scan** checks the page after every screen step for basic problems a tester can raise: images without alt text, form fields, buttons and links without a name, a missing page title or language, duplicate ids, positive tabindex, empty or skipped headings, and focusable elements inside `aria-hidden`. Each finding is logged as `[a11y] <screen>: ...` and attached to the test as *Accessibility: <screen>* (JSON, under the test's details); one is reported once per page however many steps stay on it. It never fails a test and does not check colour contrast, so it is a quick screen, not a full audit.

**Login credentials** lists the env var names the selected system/environment needs and whether each is set in `.env`. Values are never shown.

A variable that is **not** set in `.env` or the environment gets a box: type the value there and it is used for the next runs of this page session only. Values from `.env` always win, so the box is a fallback. Typed values are held in memory, never written to disk or to `.env`, masked in the console and test messages, and not sent to AI assist. A re-run of the same run reuses them; scheduled runs do not (they use `.env` only), and they are gone when the app restarts.

**Save as defaults** writes the current selections into `.env` so the app (and command-line runs) start with them. Credentials and other lines in `.env` are left untouched.

## Tests panel

Preview of what will run: test case, tags, file and step count. **Selected only** hides tests that do not match the tags. Duplicate test IDs are flagged.

## Current run

- Status badge, progress bar and per-status counts.
- Results table: one row per test and browser, with duration and the failure message.
- Live log of the run's console output (last 1500 lines; the full log is under **Console log**).
- **Cancel** stops the whole run, including browsers it started.
- **Clear** (before **Report**, shown once a run has finished) deletes that run's console log, results, report, screenshots and traces, or those of every finished run, so the next run starts on a clean page. Runs in progress or queued are never deleted; it cannot be undone and needs the delete permission. The same button is in the History tab.
- **Allure report** opens the HTML report when the Allure CLI is installed (see *History and Reports*).

Only one run at a time is allowed by default, because local runs drive real browser windows.

## Run one test from the Generator

In **Generator → Test cases**, the **Run** button runs only the open test case (using its tags, the selected environment and Chrome) and switches to this tab.
