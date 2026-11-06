# Change impact

**Which tests does this change need?** The **Impact** tab (and `python -m webapp.impact` for CI) takes a change and tells you the test cases and journeys it reaches, why, which of them it will break, and which parts of the change no test case checks at all. Then it runs just those.

## Where the change comes from

| Choice | |
|---|---|
| **Changes not committed yet** | The workspace files as they are now, compared with the last commit (`HEAD`). New files count. |
| **The last commit** | `HEAD~1` up to `HEAD`. |
| **Between two commits or branches** | Any commit, tag or branch, e.g. *Compare with* `origin/main`, *Up to* empty (the files as they are now) or another commit. |
| **Screens the app changed** | The application's developers say which screens they changed in this release. Pick them; no git needed. Combine with the above if you like. |

The workspace must be inside a git repository for the first three. Files are compared key by key, so a change is a field, not just a file:

| Kind of change | Detected as |
|---|---|
| Page file (`Pages/<SYSTEM>/...`) | each field **added**, **removed** or **changed** (locator, type, anything); the page's own settings (frame, URL) changed; a page added or removed |
| Test file (`Tests/<SYSTEM>/MasterRegressionSuite/...`) | each test case added, removed or changed |
| Common actions (`Tests/com/...`) | each common action added, removed or changed |
| Data file (`Data/TestData/<name>.json`) | the file changed |
| `Data/systemData.json` | the settings of each system that changed (they reach every test case of that system) |
| Any other workspace file (`Data/suiteConfig.json`, mappings...) | reaches every test case |

The app's own files (journeys, monitors, schedules, quarantine, heals, locator profiles, edit history) are not test changes and are ignored.

## Who a change reaches

The workspace as it is now is read, and every test case's steps are followed through the **common actions** it calls and the **test cases it reuses** (three levels deep, like the runner). A test case is reached when:

- it changed itself, or a test case it reuses changed;
- it calls a common action that changed;
- it fills or clicks a field that changed (also as a repeated key like `zip|`, and through `override` values), or uses a page whose settings changed;
- it reads a data file that changed;
- its system's settings changed.

**Will break** marks the test cases that use something the change **removed** (a field, a page, a common action, a reused test case, a data file). They are listed, and run, first.

**Not tested** marks the changes that no test case uses, for example a field added to a page that no step fills yet. Nothing will check those; that is where to write the next test.

**Journeys it reaches** lists the journeys that contain reached test cases, critical first, with how many of their test cases are reached.

**Cannot run** marks a reached test case without tags or steps (the framework only selects tagged tests).

## Run just those

Pick the environment and click **Run the N test cases**: one run per system with exactly the test cases reached (trigger `impact`), with your saved browser options. It needs the runner role.

## In CI

```
python -m webapp.impact --base origin/main                  # summary
python -m webapp.impact --base origin/main --json           # the full report
python -m webapp.impact --base origin/main --fail-on-breaks # exit 3 when a test case will break
python -m webapp.impact --base origin/main --run dev        # run only the impacted test cases
python -m webapp.impact --screen PORTAL:Payment --run uat   # the screens the app changed
```

With `--run` it calls `python -m webapp.cli_run` for each system with `--test-id` for each reached test case, so the console, report and exit codes are the same as a normal CI run. On a pull request, fetch the base branch first (`git fetch origin main`, or `fetch-depth: 0` in `actions/checkout`).

## API

`POST /api/impact` with `{"base": "origin/main", "head": "", "screens": {"PORTAL": ["Payment"]}}` returns the changes, the test cases (with reasons, *breaks*, journeys), the journeys, what is not tested, and `selection` (system → test case IDs). `POST /api/impact/run` with the same and `"env"` starts the runs.
