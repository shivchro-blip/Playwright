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
- **Clear** (before **Report**) deletes the run's console log, results, report, screenshots and traces, or, if you choose, those of every finished run. Runs in progress or waiting in the queue are never deleted, and it cannot be undone. It needs the delete permission.

Runs survive app restarts. A run that was in progress when the app stopped is shown as `error`.

## Where a test failed

A failed test names where it failed: the screen, the action and the field (for example *Screen Financial > FILL BankNme*, inside a common action or reused test if there is one). It is read from the failing step of the run's own results, so it needs no Allure CLI. You see it:

- in the **Message** column of the results tables, ahead of the error text;
- at the top of the test's details (click the row), as a trail of steps with the screenshot taken at that moment;
- in the built-in report: under each failed test, and a **Failures by screen** table, because several failures on one screen usually have one cause.

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
