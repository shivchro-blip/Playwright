# AutomateNY

JSON-driven browser test automation built on Python, pytest and Playwright, with a local web app for running tests and editing them.

Test cases and page objects are plain JSON files, so testers can write and maintain tests without writing Python. The repo ships with a small **GOOGLE** sample system (3 tests) to show how it fits together.

## Quick start (Windows)

Requires **Python 3.12+** on `PATH`.

```powershell
git clone https://github.com/shivchro-blip/Playwright.git
cd Playwright
powershell -ExecutionPolicy Bypass -File setup.ps1
start.cmd
```

`setup.ps1` creates `.venv`, installs the Python packages and Playwright Chromium, creates `.env` from `.env.example` if it doesn't exist, and adds a **Start AutomateNY** desktop shortcut (skip with `-NoShortcut`). It is safe to re-run after pulling updates.

`start.cmd` starts the web app and opens **http://127.0.0.1:8080**. It only listens on your own machine by default.

## The web app

| Tab | What it does |
|---|---|
| **Control** | Pick system, environment, tags and browsers; run and watch live progress, results and console output. Click a result row for its failure details: error, traceback, screenshots, DOM snapshot, trace, video and log. |
| **Generator** | Edit test cases and page objects (form or raw JSON), validate them, and run a single test. The scan button on a page field opens the page in headless Chromium and shows what its locator matches (0, 1 or many, with a highlighted screenshot), using the unsaved config. The optional **Recorder** (off by default; switch next to the section tabs) records a flow in a real browser with Playwright codegen and saves it as a page and a test case. |
| **History** | Past runs with details, re-run and delete; result rows open the same failure details as Control. Each test shows its recent results (flaky ones are flagged, and listed in a Flaky tests card), and **Compare** diffs a run against an earlier one: new failures, fixes, slower tests, added and removed tests. |
| **Schedules** | Start runs on a timetable (every weekday at 06:30, every 4 hours, any cron expression) while the app is running, and notify Slack, Teams, a webhook or email when they fail. Secrets stay in `.env` (`NOTIFY_*`). |
| **AI** (top bar) | Optional, off by default: Claude explains failures, suggests locator fixes (verified before you see them), drafts test cases from plain English and summarizes runs. It only suggests; everything is applied by a click and logged. Needs `ANTHROPIC_API_KEY`; secrets are redacted before sending. |
| **Activity** (top bar, admins) | Audit trail: sign-ins, refused actions and every change to runs, tests, pages, schedules and settings. |
| **Help** | Framework and app documentation (`docs/help/`). |

Every finished run gets a built-in report (**Report** button, or `runs/<id>/report.html`): summary, failures with screenshots, recent results per test and the system's trend. The Allure CLI is optional; the **Allure** button appears when it is installed.

For the sample, choose system `GOOGLE`, environment `dev`, and tag `GOOGLE` (all tests) or `smoke`.

## Sharing the app with a team

Sign-in is off on your own machine (`127.0.0.1`). For a shared server, create an admin with `python -m webapp.users add <name> --role admin` and start with `AUTOMATENY_AUTH_ENABLED=true`; sign-in is required automatically on a non-loopback `AUTOMATENY_HOST`. Roles: viewer, runner, editor, admin. Scripts use API tokens (`python -m webapp.users token <name>`). Runs started while another is in progress are queued. See Help → *Accounts, roles and the audit trail*.

## Running tests without the UI

```powershell
.venv\Scripts\python -m pytest tests/dynamic_test_suite.py -v
```

Selection comes from `.env` (`systemConfig`, `envConfig`, `runTags`, `browsers`, `headlessMode`). Any of these can be overridden by setting an environment variable of the same name for that run.

## Project layout

```
webapp/                 NiceGUI + FastAPI web app (python -m webapp)
  services/             run manager, test/page editing, system data, help
  ui/                   Control, Generator, History, Help tabs
tests/dynamic_test_suite.py   single parametrized pytest suite that runs the JSON tests
pages/, utils/          framework engine: page handling, form filling, actions, test data
workspace/
  Data/systemData.json  systems, environments, base URLs, login settings
  Data/suiteConfig.json default suite selection
  Data/rand_gen_rules.json  random test data rules
  Pages/<SYSTEM>/       page objects: field name -> locator + type
  Tests/<SYSTEM>/       test cases
  Tests/com/            common actions shared across systems
docs/help/              documentation shown in the Help tab
```

## Adding a system

1. Add an entry under `systems` in `workspace/Data/systemData.json` with at least `base_url` and `logged_in_check` per environment.
2. Add page objects under `workspace/Pages/<SYSTEM>/` (start from `workspace/Pages/templatePage.json`).
3. Add test cases under `workspace/Tests/<SYSTEM>/MasterRegressionSuite/`.
4. If the system needs a login, put the credential variable names in `systemData.json` (`username_env` / `password_env`) and the values in your local `.env`.

The new system appears in the web app's System dropdown. See the Help tab for the full test-case and page-object format.

## Configuration

| Setting | Env var | Default |
|---|---|---|
| Port | `AUTOMATENY_PORT` | `8080` |
| Host | `AUTOMATENY_HOST` | `127.0.0.1` |
| Extra allowed host names (comma-separated; needed when the app is reached by a DNS name or through a proxy) | `AUTOMATENY_ALLOWED_HOSTS` | none |
| Workspace folder | `AUTOMATENY_WORKSPACE` | `workspace/` |
| Run output folder | `AUTOMATENY_RUNS_DIR` | `<workspace>/reports/runs` |
| Allure CLI | `AUTOMATENY_ALLURE_CMD` | `allure` |

The web app has no login yet. It answers only to its own host names (`127.0.0.1`, `localhost`, the configured host and `AUTOMATENY_ALLOWED_HOSTS`) and refuses browser requests from other sites. If you set `AUTOMATENY_HOST` to a non-loopback address, anyone who can reach it can edit tests and start runs.

HTML reports need the [Allure command-line tool](https://allurereport.org/docs/install/) on `PATH`; without it, runs still work and the report link shows as unavailable.

## Dependencies

- `requirements.txt` / `requirements-web.txt` list the direct dependencies (version ranges).
- `requirements-lock.txt` pins every package, including transitive ones, with SHA-256 hashes for Windows, Linux and macOS on Python 3.12+. `setup.ps1` and CI install from it with `--require-hashes`.
- After changing a requirements file, regenerate the lock with the `uv pip compile ...` command in `requirements-dev.txt`, then commit both files.

## Development and CI

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests/unit          # fast unit tests
.venv\Scripts\python -m pytest tests/e2e           # real runs against a local test page (needs Playwright Chromium)
.venv\Scripts\ruff check webapp tests/unit tests/e2e
```

GitHub Actions (`.github/workflows/ci.yml`) runs these on Linux and Windows for every push and pull request, plus a dependency vulnerability scan (`pip-audit`). A separate, optional workflow runs the GOOGLE sample tests against the real site.

## Credentials

Credentials live only in your local `.env`, which is git-ignored. `.env.example` lists the settings with credential values blank. The web app shows whether credentials are set and never displays or edits them. For a variable missing from `.env`, the Control tab lets you type a value for the next run only: it stays in memory, is never saved, and `.env` always wins when both exist. Never commit `.env`.
