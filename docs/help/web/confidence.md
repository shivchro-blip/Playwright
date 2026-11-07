# Confidence

**Confidence** answers two questions for each environment of a system, on one screen: *is it safe to release?* and *is it working?* It reads what the Journeys, Releases and Monitors tabs already know and gives each environment one verdict, with the reasons.

## The verdicts

| Verdict | When |
|---|---|
| **Blocked** | A critical or high journey is failing there, a monitored journey is down, or the latest release check for it was rejected |
| **At risk** | Another journey is failing or only partly ran, a critical or high journey has not run recently, an incident is open, or the latest release check is NO-GO and nobody has signed it off |
| **Ready** | Every journey that ran there is passing, and nothing above applies |
| **Not known yet** | Nothing has run there yet |

Each environment's card lists the reasons (for example *Renew a licence is failing (Payment > pay) — critical*), its journeys by status, its latest release check and what its monitors see, with **Release check** and **Monitors** a click away.

## Journeys by environment

Below the cards, every journey of the system against every environment: its status there (passing, failing, partly run, not run recently, never run), where it failed, and whether a monitor watches it and what it sees. The most critical journeys come first. **Open Journeys** edits them; **What a change affects** opens Impact.

The page updates by itself when a run finishes, a monitor checks, or a journey, release or monitor is saved.

## For scripts

`GET /api/v1/confidence?system=<system>` returns the same: each environment's verdict, label, reasons, journey counts, latest release and monitor, and the journeys with their status on each environment.
