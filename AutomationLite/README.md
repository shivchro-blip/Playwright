# AutoPlay Lite

The small edition of AutoPlay for running tests that someone else wrote. Pick the system, environment and
tests on **Control**, press **Run**, watch the console and open the report and Allure when it ends.

| Section | What it is for |
|---|---|
| Control | Choose a system, environment and tests, press Run, follow the run live, open its report and Allure |
| History | Past runs, results, trends and re-runs |
| Generator | Look at and adjust test cases, pages, data sets and common actions (no Recorder, no Studio) |

## First time on a machine

1. Install Python 3.12 or newer (python.org) and tick "Add python.exe to PATH".
2. Open PowerShell in this folder and run `./setup.ps1` (creates `.venv`, installs the packages and the
   Playwright browser, and creates `.env`).
3. Put the passwords your tests need in `.env` (never in a test file).
4. Put the shared test files in `workspace/` (`Tests/`, `Pages/`, `Data/`), keeping the folder layout.

## Every day

Double-click `start.cmd` (or the "Start AutoPlay Lite" desktop shortcut). The app opens at
http://127.0.0.1:8080.

## For the person who maintains it

This folder is generated from the full AutoPlay repository: `python tools/build_lite.py` rebuilds it, and
`python tools/build_lite.py --check` tells whether it is up to date. Do not edit the code here; change the full
app and rebuild. `workspace/` is the only part that belongs to the team: a rebuild never overwrites it.
