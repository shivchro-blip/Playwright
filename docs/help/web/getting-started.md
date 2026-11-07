# Getting Started

AutoPlay runs JSON-driven browser tests (Python, pytest and Playwright). The web app is where you pick what to run, watch it live, and edit tests and pages.

## Start the app

First time on a machine (needs Python 3.12+ on PATH), from the project folder:

```
powershell -ExecutionPolicy Bypass -File setup.ps1
```

This creates `.venv`, installs the packages and Playwright Chromium, creates `.env` from `.env.example` if missing, and adds a **Start AutoPlay** desktop shortcut. Safe to re-run after pulling updates.

Then start the app with the shortcut or `start.cmd` (same as `.venv\Scripts\python -m webapp`).

Open **http://127.0.0.1:8080** (`start.cmd` opens it for you). The app only listens on your own machine by default.

| Setting | Env var | Default |
|---|---|---|
| Port | `AUTOPLAY_PORT` | `8080` |
| Host | `AUTOPLAY_HOST` | `127.0.0.1` |
| Workspace folder | `AUTOPLAY_WORKSPACE` | repo `workspace/` |
| Run output folder | `AUTOPLAY_RUNS_DIR` | `<workspace>/reports/runs` |
| Allure CLI | `AUTOPLAY_ALLURE_CMD` | `allure` |

## Tabs

- **Control** - pick a system, environment, tags and browsers, then run. Shows live progress, results and the console log.
- **Generator** - edit test cases and page objects (POM JSON).
- **History** - every past run, with details, re-run and delete.
- **Help** - these pages.

The other tabs (Journeys, Releases, Monitors, Impact, Schedules) have their own help pages. The address follows the tab you are on (`http://127.0.0.1:8080/#history`), so a bookmark, a link you share or a reload opens on the same tab, and the browser's Back and Forward move between tabs.

## Find anything: Ctrl+K

Press **Ctrl+K** (**Cmd+K** on a Mac), or **Search** in the header, anywhere in the app. One box finds:

- any tab, and actions such as **Start a run**, **New test case** and **Open trends**;
- every test case, page, field and journey in the workspace (a test case opens in the Generator's editor, a page in the Pages tab, a field opens its page with the field picked out);
- the latest runs (opens the run's details in History).

Type a few letters: the whole name, its start, the start of a word in it, or just the letters in order (`tcsm` finds `TC-SMOKE-...`). Several words narrow it down, and a word can also match the details line (`login portal`). **Up** and **Down** move, **Enter** opens, **Esc** closes. An empty box lists the tabs and the latest runs.

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
