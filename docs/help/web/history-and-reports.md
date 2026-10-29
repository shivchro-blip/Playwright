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

## Allure reports

Install the Allure CLI (needs Java), for example:

```
scoop install allure
```

or set `AUTOMATENY_ALLURE_CMD` to the full path of `allure.bat`. New runs then generate `allure-report/` automatically and the **Allure report** button opens it. Without the CLI, runs still keep their raw `allure-results/`.

## Videos and traces

Turn on **Record video** or **Playwright trace** under *Browser & artifacts* before running. They are attached to the Allure results of the run.
