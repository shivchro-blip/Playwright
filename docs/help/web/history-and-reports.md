# History and Reports

Every run gets its own folder:

```
<workspace>/reports/runs/<run id>/
  run.json         settings, status and per-test results
  console.log      full console output
  allure-results/  raw Allure data
  allure-report/   HTML report (only when the Allure CLI is installed)
```

## History tab

- Filter by system, status or text.
- Select a run to see its settings, results and log.
- **Re-run** starts a new run with the same settings.
- **Delete run…** (before **Report**; it was called *Clear*) deletes the run's console log, results, report, screenshots and traces, or, if you choose, those of every finished run. Runs in progress or waiting in the queue are never deleted, and it cannot be undone. It needs the delete permission.

Runs survive app restarts. A run that was in progress when the app stopped is shown as `error`.

## Where a test failed

A failed test names where it failed: the screen, the action and the field (for example *Screen Financial > FILL BankNme*, inside a common action or reused test if there is one). It is read from the failing step of the run's own results, so it needs no Allure CLI. You see it:

- in the **Message** column of the results tables, ahead of the error text;
- at the top of the test's details (click the row), as a trail of steps with the screenshot taken at that moment;
- in the built-in report: under each failed test, and a **Failures by screen** table, because several failures on one screen usually have one cause.

## Why it failed (failures grouped by cause)

A run's details open with **Why it failed** when tests failed. Failures that have the same cause are one group:
"12 tests fail on Applicant > zip: element not found in time" is one fix, not twelve failures to read. The cause is
where the test failed (the screen and field of the failing step) and the kind of error (element not found in time,
several elements match, hidden or disabled, expected text missing, page or field missing, site unreachable, sign-in
failed, test data, a failed check, or the error's first line with the changing parts taken out).

Each group says what the evidence points to:

- **One field in many tests**: fix the locator on the page once.
- **Only one environment**: the same tests pass in another environment, so look at the environment, its data or its
  latest deploy before the tests.
- **New since a run**: the tests passed in the run before it. The page and test case files edited after that last
  passing run are listed with when they were saved.
- **Comes and goes**: the tests also passed in between (flaky, or timing), or **failing in every recent run**.

A **Try** line suggests the next step, and **Error** shows one of the messages. Every test of the group is listed.
The groups use what runs already keep, so they work on earlier runs too.

From scripts: `GET /api/v1/runs/{id}/causes` (one run) and `GET /api/v1/insights/failures?system=S&env=E&runs=10`
(the latest finished runs of a system, grouped across runs).

## Allure reports

Install the Allure CLI (needs Java), for example:

```
scoop install allure
```

or set `AUTOPLAY_ALLURE_CMD` to the full path of `allure.bat`. New runs then generate `allure-report/` automatically and the **Allure report** button opens it. Without the CLI, runs still keep their raw `allure-results/`.

## Videos and traces

Turn on **Record video** or **Playwright trace** under *Browser & artifacts* before running. They are attached to the Allure results of the run.

In a test's failure details the video plays in place. For a trace, **Open in trace viewer** opens Playwright's trace viewer (a window, with the timeline, DOM snapshots, network and console for every step). It opens on the computer that runs the app, so it is offered only when the app listens on this machine alone (`AUTOPLAY_HOST=127.0.0.1`); on a shared server, download the trace and open it with `playwright show-trace <file>` or drop it on trace.playwright.dev (it is processed in your browser, not uploaded).

## Hiding sensitive values

Test data looks real, and logs and reports get shared. Social Security numbers (`123-45-6789`) and card numbers (valid ones only, so run ids and timestamps are left alone) are replaced in the console, the results table, the built-in report and the failure details shown in the app. Add your own rules, or turn the built-in ones off, in `Data/maskPatterns.json` in the workspace:

```json
{ "builtin": ["ssn", "card", "routing"],
  "custom": [ { "name": "policy", "pattern": "POL-\\d{6}", "replacement": "POL-******" } ] }
```

- `builtin` lists the rules to use: `ssn`, `card`, and `routing` (nine-digit bank routing numbers with a valid checksum, off by default because any nine digits can look like one). `"builtin": []` turns them off. Without the file, `ssn` and `card` are on.
- Each custom rule is a regular expression (`pattern`), an optional `name` and an optional `replacement` (default `[masked <name>]`).
- Changes apply to new output at once. A bad file is ignored with a warning in the app's log.
- **Not changed:** screenshots, DOM snapshots, videos, traces and the raw files in the run folder. Treat those as sensitive.

## Flaky tests and quarantine

The **Flaky tests** panel lists tests whose result flipped between pass and fail in their recent runs, or that passed only after a retry. For each, **Quarantine** keeps the test case running but stops its failures from failing the run: it is reported as *xfailed* with your reason, the run stays green, and a pass shows as *xpassed*. **Release** puts it back to normal. The same **Quarantine / Release** button is in the Generator's test case editor. Quarantined test cases are listed in `Data/quarantine.json` in the workspace (a test case with a data set is matched by its ID without the `[set]` part). Quarantine hides a problem: fix the test or the portal, then release it.

## Step by step (time travel)

Switch on **Step pictures** (Control > run settings, or `stepCapture=true` in `.env`) and every run saves the page
after each step of each test: a picture, its HTML, its address and title, and the step's time. A test's details then
show **Step by step**:

- a slider (and Previous / Next) through the steps, with each step's outcome, time and page address;
- beside each picture, the same step of the **last run where the test passed** (same system, environment and
  browser, with step pictures);
- above them, the steps where the two runs part: **failed here**, **not reached in this run**, **ended on another
  page**, **page title changed**, **slower** (at least twice as slow and 2 seconds more). The slider opens on the first
  of them.

**Page HTML** opens the page as it was (shown sandboxed: its scripts do not run). The files are kept with the run
under `allure-results/steps/` and go when the run is cleared. A picture is about 50-150 KB per step: switch it on for
the runs you want to look into, or for a nightly run whose failures you want to understand without running again.

## A test's timeline

**Timeline** shows one test case's whole story on one screen, newest first. Open it from a test's **Details** (in a run), from **Timeline** in the Flaky tests panel, or from the Generator's test case editor.

- **On top, how it is doing**: passing, failing, flaky, just broke or just fixed; its pass rate over its recent results; how often it changed result; its typical time, flagged **slowing down** when its latest passing runs take a quarter longer than the ones before; its recent results as coloured squares; and its quarantine, if any.
- **Then everything that happened to it**: each result with the environment, browser and time, and for a failure the step it failed on and why (for example `step 2 of 3: Payment · Timeout 30000ms`), or that it passed only after a retry; the heals and element-identity fallbacks it needed in that run; when it was quarantined or released, by whom and why; and when the test case was saved.
- Pick an environment to see only its runs. **Quarantine** / **Release** works right there, and the story updates.
- A test case with data sets (`TC-1[gold]`) is shown with all its sets; each result says which set it was.
- The same is available to scripts: `GET /api/v1/tests/<test id>/timeline?system=<system>&env=<env>`.

## Trends

**Trends** (top right of the Runs list) shows how the suite is doing over time for a system, and optionally one environment, over the last 7 to 90 days:

- **Pass rate by day**: a bar per day, green for passed and red for failed test results, with the overall rate for the period. Days without a run are empty.
- **Slowest tests**: the tests with the longest average duration over their last 20 runs, with the slowest and latest time, so creeping slowness shows before it causes timeouts.
- **Where tests fail**: failures of the last 15 runs grouped by screen, field and action, so one broken locator or one unstable screen stands out from a long list of red tests.


## Run history index

Every run is also recorded in a small database beside the runs folder (`runs.results.db` next to the `runs` folder), so journey status, release checks, recent heals, schedule history and flaky tests look up only the runs they need instead of reading every run the app holds. It needs no setup and nothing to install.

- **The run folders stay the source of truth.** The index is updated whenever a run is saved, finished or deleted, and brought up to date from the folders each time the app starts (only changed folders are read). Delete `runs.results.db` to rebuild it from the folders; an unreadable one is set aside as `runs.results.db.bad` and rebuilt automatically.
- **From the API:** `GET /api/runs` takes optional `system`, `env`, `since` and `until` (ISO time of creation), `finished=true`, `trigger` and `limit`, newest first. `GET /api/tests/<test id>/history?system=&env=&limit=20` lists a test's latest results across runs: run, time, browser, outcome, duration and retries.

```
curl "http://host:8080/api/runs?system=PORTAL&env=uat&finished=true&limit=10"
curl "http://host:8080/api/tests/TC-LOGIN-001/history?env=uat"
```

## Live updates and the events API

The pages update when something changes rather than on a timer: a run that starts, reports a result, moves to its next step, prints to the console, finishes or is deleted; a workspace file written from the app; a monitor or schedule that starts a check. History, Journeys, Monitors and Schedules refresh within a moment of the change, and Control and the Generator's run panel follow a run step by step. A slow timer still refreshes now and then for what no event covers (a schedule's next run moving with the clock, a file edited outside the app).

Scripts and dashboards can follow the same changes with `GET /api/events`:

- `since`: the `seq` from your last call (0 the first time). Events come back oldest first, each with `seq`, `topic`, `at` (Unix time) and its details (for runs: `id`, `change`, `status`, `system`, `env`, `trigger`).
- `topics`: a comma list of `run`, `workspace`, `monitor`, `schedule` (all when left out).
- `wait`: up to 30 seconds to hold the call until something new happens (long polling).
- The answer is `{"seq", "events", "missed"}`. Pass `seq` as `since` next time. `missed` is true when you fell further behind than the app keeps (the last 1000 events): read the current state again, for example with `GET /api/runs`.

```
curl "http://host:8080/api/events?since=0&topics=run&wait=25"
```
