# Getting Started

AutomateNY runs JSON-driven browser tests (Python, pytest and Playwright). The web app is where you pick what to run, watch it live, and edit tests and pages.

## Start the app

First time on a machine (needs Python 3.12+ on PATH), from the project folder:

```
powershell -ExecutionPolicy Bypass -File setup.ps1
```

This creates `.venv`, installs the packages and Playwright Chromium, creates `.env` from `.env.example` if missing, and adds a **Start AutomateNY** desktop shortcut. Safe to re-run after pulling updates.

Then start the app with the shortcut or `start.cmd` (same as `.venv\Scripts\python -m webapp`).

Open **http://127.0.0.1:8080** (`start.cmd` opens it for you). The app only listens on your own machine by default.

| Setting | Env var | Default |
|---|---|---|
| Port | `AUTOMATENY_PORT` | `8080` |
| Host | `AUTOMATENY_HOST` | `127.0.0.1` |
| Workspace folder | `AUTOMATENY_WORKSPACE` | repo `workspace/` |
| Run output folder | `AUTOMATENY_RUNS_DIR` | `<workspace>/reports/runs` |
| Allure CLI | `AUTOMATENY_ALLURE_CMD` | `allure` |

## Tabs

- **Control** - pick a system, environment, tags and browsers, then run. Shows live progress, results and the console log.
- **Generator** - edit test cases and page objects (POM JSON).
- **History** - every past run, with details, re-run and delete.
- **Help** - these pages.

## Workspace layout

```
workspace/
  Data/systemData.json     systems, environments, base URLs, login settings
  Data/rand_gen_rules.json random test data rules (autoGen)
  Pages/<SYSTEM>/**.json   page objects: field name -> locator + type
  Tests/<SYSTEM>/MasterRegressionSuite/**.json   test cases
  Tests/com/**.json        common actions shared by all systems
```

Credentials live in `.env` (for example `MYAPP_DEV_USERNAME=` / `MYAPP_DEV_PASSWORD=`). The app shows whether they are set but never displays or edits them.

## Your first run

1. **Control** tab: System `GOOGLE`, Environment `dev`, Tags `smoke`.
2. Leave **Headless** off to watch the browser.
3. Click **Run**. Results appear per test and browser; click **Console log** for the full output.
