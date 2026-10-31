# Run in CI

The same engine that runs from the Control tab runs without the web app, for builds and scripts:

```
python -m webapp.cli_run --system NYBE2 --env dev --tags smoke --browsers chromium
```

It streams the console, builds the same built-in report, and ends with the exit code a pipeline needs.

| Option | Meaning |
|---|---|
| `--system`, `--env`, `--tags` | What to run. Default to `systemConfig`, `envConfig` and `runTags` in `.env`. |
| `--browsers` | Comma-separated, default `chromium`. |
| `--workers N` | Parallel workers (pytest-xdist). |
| `--test-id ID` | Only this test case (repeatable); its tags must still match. |
| `--headed` | Show the browser. Without it the run is headless. |
| `--log-level` | `ERROR`, `WARNING`, `INFO` (default) or `DEBUG`; same as the Control tab's *Console log level*. |
| `--workspace`, `--out` | Workspace folder (default `AUTOMATENY_WORKSPACE` or the repo's) and where run folders go. |
| `--timeout-minutes N` | Cancel the run after N minutes. |
| `--quiet` | Do not stream the console. |

**Exit code:** `0` all passed, `1` tests failed, `2` anything else (no test matched the system, environment and tags; a setup error; a crash), `130` interrupted. A run that selects no tests is exit `2` with the reason in the summary, not an ordinary failure.

**Output** (in the run folder under `--out`): `report.html` (the built-in report: failures with where they failed and a screenshot, trend, flaky tests), `summary.md`, `allure-results/` and any traces or videos. `summary.md` is also appended to `$GITHUB_STEP_SUMMARY` when that is set, and on GitHub Actions each failure is annotated with the screen and field it failed on.

**Logins** come from the environment (use your CI's secrets) or the workspace `.env`. The variable names are `username_env` and `password_env` in `Data/systemData.json`.

## GitHub Actions

`docs/ci/github-actions-suite.yml` in the repository is a ready workflow: copy it to `.github/workflows/test-suite.yml` and change the lines marked `CHANGE` (the default system, environment and tags, the schedule, the paths that trigger a run, and the secret names). It runs on commit (only when tests, pages or the framework change), on a weekday schedule and from the **Run workflow** button, uploads the report, and fails the job when a test fails.

A cloud runner can only reach systems that are reachable from the internet. For an internal portal use a self-hosted runner inside your network (`runs-on: [self-hosted]`).

## Other CI systems

Anything that can run Python works the same way: install the locked dependencies (`pip install --require-hashes -r requirements-lock.txt`), install a browser (`python -m playwright install chromium`; add `--with-deps` on Linux), set the secrets as environment variables, run `python -m webapp.cli_run ...`, and archive the run folder. Use the exit code to pass or fail the build.
