# Automations

**Automations** puts "when something happens, do something" in one place. A rule says *when* and *then*:

| When | Narrow it to |
|---|---|
| **A deploy is announced** (your CI/CD calls the webhook below) | a system, an environment |
| **A run finishes** | a system, an environment, and *any* / *failed* / *passed* |
| **A monitored journey goes down** | a system, an environment |
| **A monitored journey recovers** | a system, an environment |

| Then | Notes |
|---|---|
| **Run test cases by tag** | e.g. `smoke`; with the saved browser and login settings, like any run from the app |
| **Run journeys** | listed by id, or all of the system's when none are listed |
| **Send a notification** | Slack, Teams or webhook; their addresses come from `.env` (`NOTIFY_SLACK_URL`, `NOTIFY_TEAMS_URL`, `NOTIFY_WEBHOOK_URL`) |

A rule can have several *thens*; they are done in order, and one that fails (a full queue, an unset channel) does not stop the next. When a *then* leaves the system or environment out, it uses the event's: one rule "after a deploy to any environment, run the smoke tests" runs them on whichever environment was deployed.

Examples:

- *Smoke after deploy*: when a deploy is announced on PORTAL/uat, run tests tagged `smoke` and notify Slack.
- *Second look*: when a run on PORTAL fails, run the journeys of PORTAL on the same environment.
- *Page someone*: when a monitored journey goes down on prod, notify Teams.

Runs started by a rule are marked `automation:<rule>` (you can filter History or `GET /api/v1/runs?trigger=automation:<rule>` by it) and **never set off a "run finished" rule themselves**, so a rule cannot loop.

Each rule shows when it last fired; **Recent** lists the latest firings with what each *then* did (and the run it started). Switch a rule off without deleting it with its switch. Changing rules needs the **schedule** permission.

## After a deploy: the webhook

Have your pipeline call this after each deploy (with sign-in on, use a token whose user may run tests):

```
curl -X POST http://host:8080/api/v1/automations/deploy \
  -H "Content-Type: application/json" -H "Authorization: Bearer any_..." \
  -d '{"system": "PORTAL", "env": "uat", "build": "482", "by": "github-actions"}'
```

The answer lists the rules it set off. The environment must exist for the system in `systemData.json`.

## Schedules and monitors

Schedules (run at a time) and monitors (check journeys every few minutes) keep their own tabs; the Automations tab lists them under **Also running on their own**, with links. Rules are stored in `Data/automations.json` in the workspace.

## For scripts

`GET /api/v1/automations` (rules and recent firings), `PUT /api/v1/automations/<id>` (create or replace a rule: `{name, enabled, when: {on, system, env, status}, do: [...]}`), `DELETE /api/v1/automations/<id>`.
