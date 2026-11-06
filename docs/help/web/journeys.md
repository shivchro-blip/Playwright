# Journeys

A **journey** is a business transaction your system exists to serve: a business registration, an annual filing, a licence renewal, a payment. It groups the test cases that prove it, and has a criticality and an owner. The **Journeys** tab answers the question people outside QA ask: *which of our important transactions work right now, on each environment?*

## Create one

1. Open **Journeys**, pick the system, and click **New journey**.
2. Give it a **name** in business terms, a **criticality** (critical, high or normal), an **owner** (team or person) and, if you like, a sentence on what the transaction is for.
3. Pick the **test cases that prove it**. A test case can belong to more than one journey. The tab lists test cases that are in no journey yet, so nothing important is left out.

Journeys are saved in `Data/journeys.json` in the workspace (with edit history, like pages and test cases). Test cases, pages and runs are not changed.

## What the table shows

One row per journey, critical ones first, with a column per environment of the system:

| Status | Meaning |
|---|---|
| **passing** | The latest result of every test case of the journey on that environment passed, within the last 7 days. |
| **failing** | At least one test case failed or errored in its latest run there. *Where it fails* shows the screen, field and action. |
| **partly run** | Some of its test cases have never run on that environment. |
| **not run recently** | Everything passed, but the oldest of those results is more than 7 days old. |
| **never run** | No test case of the journey has run there yet. |

Under the status: the share of the journey's recent runs (up to 10) in which every test case that ran passed. A test case with several data sets or browsers counts as failing in a run if any of them failed.

**Coverage** is the share of fields, on the pages the journey uses, that its steps fill or click. That includes the common actions and reused test cases it calls, and values changed with `override`. Low coverage on a critical journey shows where a page has fields no test touches.

## Run a journey

**Run** runs all of a journey's test cases on the environment you pick, with the browser options saved in Settings, then shows the run on the Control tab. Missing logins come from *Login credentials* or the secret store, as for any run.

Click a row for the details: each test case's latest result per environment (click the run id to open it) and coverage per page.

## API

- `GET /api/journeys?system=PORTAL`: every journey with its status per environment, the latest result of each test case, and coverage.
- `POST /api/journeys/<id>/run?env=dev`: runs the journey (needs the *runner* role).

## Safe to monitor

Turn on **Safe to monitor** in a journey's dialog when its test cases create no real records. Only such journeys can be watched by a monitor, which runs them on production every few minutes. See the Monitoring help page.
