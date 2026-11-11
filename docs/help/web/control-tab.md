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

## Run what matters (Plan)

**Plan** (next to Run) picks the test cases worth running in the time you have, on the chosen system and
environment, and says why each one is in or left out:

| Risk | Why it counts |
|---|---|
| The change breaks it / reaches it | With **Change since** a git commit or branch: the test cases the change reaches (see Change impact) |
| Critical journey | Always in, even past the budget: the plan says when they alone go over it |
| High journey, journey | Business weight from Journeys |
| Failed in its last run | On this environment |
| Flaky lately | Its result changed back and forth in recent runs |
| Never run here / not run for days | Unknown risk |

After the critical journeys' test cases, the rest are taken by risk per second of run time until the budget is spent;
a test case's time is its median duration in recent runs (or the system's typical test time when it has none).
**Left out** lists the rest with the reason ("over the budget", "nothing points to a risk"), and a risky test case
that did not fit is named at the top. **Run these N** starts a run of exactly the planned test cases.

From CI, for a pull request within a time budget:

```
curl -X POST <app>/api/v1/plans/run -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"system": "PORTAL", "env": "uat", "budget_minutes": 10, "base": "origin/main"}'
```

`POST /api/v1/plans` returns the plan without running it.

## Current run

- Status badge, progress bar and per-status counts.
- Results table: one row per test and browser, with duration and the failure message.
- Live log of the run's console output (last 1500 lines; the full log is under **Console log**).
- **Cancel** stops the whole run, including browsers it started.
- **Clear** (before **Report**, shown once a run has finished) deletes that run's console log, results, report, screenshots and traces, or those of every finished run, so the next run starts on a clean page. Runs in progress or queued are never deleted; it cannot be undone and needs the delete permission. The same button is in the History tab.
- **Allure report** opens the HTML report when the Allure CLI is installed (see *History and Reports*).

## Pause on failure: keep the browser open, fix the test, resume

A step that fails normally ends the test and closes the browser. With **Pause on failure** (Browser & artifacts here; the **Debug** button next to **Run** in the Generator, or a tick in **From step...**) the test **waits** instead, with its browser, the page as it was, and everything it saved earlier (`DICT` values, the login) still alive. The run shows **Paused: waiting for you**, with the step and field that failed, the error, a picture of the page, and:

| Button | What it does |
|---|---|
| **Open test case / Open page** | Jump to the Generator to fix the test case or the page (the field is filtered for you). Save the file. |
| **Run '<field>' again** | Reads the test case, pages, common actions and reusable tests from disk again, runs the failed field again, and carries on, in the same browser. Fields before it do not run again. |
| **Run the step again** | The same, from the step's first field. Fields that clicked or submitted something run twice. |
| **Skip this field** | Leaves the failed field out and carries on with the next. |
| **End the test** | The failure stands: the test fails as usual and the browser closes. |

What a fix can change while paused: page files (locators, fields), the test case's steps and form data, common actions, reusable tests. It cannot change `.env` values, logins, browser settings (headless, size, slow-mo) or the framework's own code: those need a new run. If the edited file cannot be read (half-saved JSON), the panel says so and the run keeps waiting. If you change the steps *before* the failed one, the resume point may not match: use **Run the step again**. If **Headless** is on there is no browser window, only the picture; turn Headless off to look and click in the browser.

Limits: it runs one test at a time (Parallel workers must be 1), on this machine only (not a remote runner), and only for runs you start yourself in the app: schedules, monitors, pipelines, the REST API and `.env` can never turn it on, and it is never saved by **Save as defaults**. A paused run holds the run slot, so other runs queue behind it. It waits up to 30 minutes (`AUTOPLAY_DEBUG_TIMEOUT_MINUTES`), then ends the test as failed; closing the browser window, or **Cancel**, ends it too. A run that paused is not a clean result: it is marked in its log and `run.json` (`debug_pauses`) and a pass after a fix is a pass of the fixed test.

## Clear all

**Clear all** (top bar, on every tab) deletes everything previous runs left behind so the next run starts clean. It first lists what will go and what stays, and you confirm. It needs the delete permission, and it is written to the Activity trail.

Deleted from disk, not only hidden:

- every finished run's folder under the runs folder: console log, results, report, screenshots, traces and saved states for *From step* (so a saved state is gone too), and its entry in History, Control and the Generator (the run shown under a test case's toolbar);
- leftover run folders no run owns (for example from a crash). Only folders named like a run and holding a run's files are touched;
- the suite's own files: `<workspace>/logs/automation.log*`, `logs/context_log_*.json`, and `reports/videos`, `reports/downloads`, `reports/screenshots` and `reports/traces` in the app's folder.

Kept: your tests, pages, data sets and input files (`Data/`), `.env` and settings, saved logins and the secret store, users and API tokens, the audit trail, schedules, monitors, release packs, quarantine and heal decisions, edit history, source code and dependencies. A run that is queued or running is kept with its folder, and the shared logs, videos, downloads, screenshots and traces are left alone until it ends, because a running test writes to them.

When it finishes, a message says how much was deleted and lists any file it could not delete (for example one another program has open) with the reason. A run whose folder could not be removed completely stays listed in History; close what uses the file and run Clear all again.

The **Clear** button next to **Report** still deletes just the run on show, or every finished run, without touching the shared files.

Only one run at a time is allowed by default, because local runs drive real browser windows.

## Run one test from the Generator

In **Generator → Test cases**, the **Run** button runs only the open test case (using its tags, the selected environment and Chrome) and switches to this tab.
