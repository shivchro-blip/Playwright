# Control Tab

## Run setup

| Control | What it does |
|---|---|
| **System** | Folder under `Tests/` and `Pages/`, and the entry in `systemData.json`. |
| **Environment** | Environment key under that system in `systemData.json` (base URL, login). A test case's own `"env"` wins over this. |
| **Tags** | Only test cases with at least one matching tag run (case-insensitive). No tags = nothing runs. Type to add a tag that is not in the list. |
| **Browsers** | `chrome`, `msedge`, `chromium`, `firefox`, `webkit`. Each selected test runs once per browser. |
| **Parallel workers** | More than 1 runs tests in parallel with pytest-xdist. Live framework prints are hidden in parallel mode; results still update. |
| **Console log level** | What the run prints: *Errors only*, *Warnings and errors*, *Info* (default) or *Debug*. It sets pytest's console level and the framework loggers together (`LOG_LEVEL`, `TestSuiteLoggerLevel`, `PageLoggerLevel`, `FormHandlerLoggerLevel`), so it takes precedence over per-module levels in `.env` for runs started here. Result lines (PASSED/FAILED) always show. *Save as defaults* keeps the choice. |

**Browser & artifacts**: headless, maximized window, slow-mo, page wait condition, video recording, Playwright trace and snapshots. These apply to this run only.

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
- **Allure report** opens the HTML report when the Allure CLI is installed (see *History and Reports*).

Only one run at a time is allowed by default, because local runs drive real browser windows.

## Run one test from the Generator

In **Generator → Test cases**, the **Run** button runs only the open test case (using its tags, the selected environment and Chrome) and switches to this tab.
