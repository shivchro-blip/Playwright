# Schedules and notifications

A schedule starts a run on its own, for example the smoke tests every weekday at 06:30, and can tell Slack, Microsoft Teams, a webhook or email how it went.

## Create a schedule

1. Open the **Schedules** tab and click **New schedule**.
2. **What to run**: system, environment, tags and browsers, plus Headless (on by default: nobody is watching), Playwright trace and Record video. The dialog shows how many test cases the tags match.
3. **When**: every day, weekdays or weekends at a time, every N hours, or a custom cron expression (`minute hour day-of-month month day-of-week`, e.g. `0 */4 * * *`). The next run time is shown as you type.
4. **Notify**: when a run fails (failed or error), after every run, or never; then pick the channels.
5. **Save**. **Run now** on the list starts a schedule straight away; click a row to edit, pause (Enabled) or delete it.

Schedules are saved in `Data/schedules.json` in the workspace. Runs they start appear in **History** with a *scheduled* tag, and the run details say which schedule started them.

## Good to know

- Schedules fire **only while the web app is running**, in this computer's time zone. A time missed while the app was stopped is not caught up.
- One run at a time: a schedule that comes due while another run is in progress waits and starts as soon as that run finishes.
- An app restart within the same minute does not start the same schedule twice.

## Channels

Webhook URLs and the SMTP password are **secrets**, so they live in `.env` (or the environment), never in the schedule. A schedule names the key; the dialog only shows whether it is set. Keys must start with `NOTIFY_`.

| Channel | `.env` | Message |
|---|---|---|
| Slack | `NOTIFY_SLACK_URL` (an incoming webhook) | Text with status, failed tests and the report link |
| Microsoft Teams | `NOTIFY_TEAMS_URL` (a Workflows "post to a channel when a webhook request is received" URL) | Adaptive card with the same content and an *Open report* button |
| Webhook | `NOTIFY_WEBHOOK_URL` | JSON: `{"event": "run.finished", "schedule": ..., "run": {id, status, counts, failed, report_url, ...}}` |
| Email | `NOTIFY_SMTP_HOST`, `NOTIFY_SMTP_PORT` (587), `NOTIFY_SMTP_TLS` (`starttls`, `ssl` or `none`), `NOTIFY_SMTP_USER`, `NOTIFY_SMTP_PASSWORD`, `NOTIFY_SMTP_FROM` | Plain-text summary to the addresses you enter |

Use **Send test notification** in the dialog to check the setup. Each run's delivery results are saved next to it (`notifications.json`).

Messages include the names of failed tests and the first line of each error, which can contain text from the tested pages. Report links use `AUTOMATENY_PUBLIC_URL` (default `http://127.0.0.1:<port>`); set it to the address your team uses to open the app.
