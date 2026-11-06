# Monitoring

A **monitor** runs some of your journeys on one environment (usually production) every few minutes, and tells you when one goes **down** and when it **recovers**. It keeps each journey's **uptime** over the last 24 hours, 7 days and 30 days, and a list of **incidents** (when it went down, when it came back, how long it lasted, and where it failed).

Testing before a release says the code was fine when it shipped. Monitoring says the transaction still works now, for real users, after config changes, data changes and outages in systems you depend on.

## 1. Mark the journeys that are safe to monitor

A monitor runs against the live system many times a day, so it may only run journeys whose test cases **create no real records**: searching, signing in, looking up a filing, reaching a payment page without paying. On the **Journeys** tab, edit the journey and turn on **Safe to monitor**. The journey then shows a *monitor-safe* tag.

A monitor refuses journeys that are not marked. Marking one is a promise, so check its test cases first.

## 2. Create a monitor

On the **Monitors** tab click **New monitor** and choose:

| Setting | |
|---|---|
| **Name** | For example *Production filings*. |
| **System, Environment** | Where to check. The environment needs its URL and login in `systemData.json`, and the login values in `.env` or the secret store. |
| **Check** | Every 5, 10, 15, 30, 60, 120 or 240 minutes. |
| **Journeys to watch** | Only journeys marked safe to monitor are offered. |
| **Tell** | Slack, Teams, a webhook or email (the same channels as schedules, with the secrets in `.env`). |
| **Enabled** | Turn off to pause it without losing its history. |

Monitors are saved in `Data/monitors.json`. Changing them needs the editor role (the same permission as schedules); **Check now** needs the runner role.

## How a check works

- A check is one run with the test cases of all the watched journeys, on the first browser in your saved settings, with trigger `monitor:<id>`. It shows in History like any other run.
- A new check never starts while the previous one is still running, so a slow system does not pile up runs.
- After the check each journey is **Up** (all its test cases passed), **Down** (one failed) or **Unknown** (they did not all run, or the run itself broke). Unknown checks do not count towards uptime and do not open or close incidents.
- The monitor sends a message **only when something changes**: a journey goes down (with where it failed and a link to the report), or recovers (with how long it was down). Repeated failed checks while it is still down send nothing.

Monitors run while the web app runs (like schedules). If `AUTOPLAY_SCHEDULER_ENABLED` is off, neither runs on its own; **Check now** still works.

## API

| Call | |
|---|---|
| `GET /api/monitors` | Every monitor with each journey's state, since when, uptime for 24h / 7d / 30d, and recent incidents. |
| `GET /api/monitors/{id}` | One monitor. |
| `POST /api/monitors`, `PUT /api/monitors/{id}`, `DELETE /api/monitors/{id}` | Create, change, delete. |
| `POST /api/monitors/{id}/check` | Check now. `409` while the previous check is still running. |

Each check and incident is kept in `reports/monitors/<id>.json` (the newest 5000 checks).

## Alerts

Messages say *DOWN* or *RECOVERED*, the monitor, system and environment, each journey that changed, and a link to the run's report (when `AUTOPLAY_PUBLIC_URL` is set). Webhook messages carry the same as JSON: `{"event": "monitor.change", "monitor", "system", "env", "run", "report_url", "changes": [{"journey", "name", "change", "detail"}]}`.
