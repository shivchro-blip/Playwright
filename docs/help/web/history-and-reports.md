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
- **Delete** removes the run folder (not allowed while it is running).

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

or set `AUTOMATENY_ALLURE_CMD` to the full path of `allure.bat`. New runs then generate `allure-report/` automatically and the **Allure report** button opens it. Without the CLI, runs still keep their raw `allure-results/`.

## Videos and traces

Turn on **Record video** or **Playwright trace** under *Browser & artifacts* before running. They are attached to the Allure results of the run.
