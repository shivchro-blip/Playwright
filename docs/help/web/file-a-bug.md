# File a bug from a failed test

When a test fails, **File a bug** (top right of the failure details) writes the failure up and creates the issue in GitHub Issues or Jira, so the person who reports it does not retype the error, the step and the links.

## Set it up

Add these to the workspace `.env` (or the environment of the app). They are read only when you file a bug and are never shown in the app. A button appears only when the tracker is set up.

```
ISSUE_TRACKER=github              # or jira

# GitHub Issues
ISSUE_GITHUB_REPO=acme/portal     # owner/repo
ISSUE_GITHUB_TOKEN=...            # a token that can create issues in that repository
ISSUE_GITHUB_LABELS=bug,autoplay  # optional
ISSUE_GITHUB_API_URL=             # optional: GitHub Enterprise, e.g. https://git.acme.com/api/v3

# Jira
ISSUE_JIRA_URL=https://acme.atlassian.net
ISSUE_JIRA_PROJECT=QA
ISSUE_JIRA_TYPE=Bug               # optional, default Bug
ISSUE_JIRA_USER=you@acme.com      # Jira Cloud: your email. Leave empty for a Jira Server personal access token
ISSUE_JIRA_TOKEN=...              # the API token
```

## What it does

1. Click **File a bug** on a failed test. You see the title and the description before anything is sent, and can edit both.
2. The description has the test case, run, system and environment, browser, the failing screen, action and field, the error, the first lines of the traceback (values hidden by the masking rules), links to the run report and up to three screenshots, and how to reproduce.
3. **File the bug** creates the issue and shows its number or key with a link. Filing needs the *editor* role and is recorded in Activity.
4. The issue is remembered in the run's folder. Opening the same failure again says a bug was filed and shows its link; you can still **File another**.

The report and screenshot links use `AUTOPLAY_PUBLIC_URL`, so set it to the address people use to reach the app (for example `https://autoplay.acme.com`); with the default `http://127.0.0.1:8080` they only open on your own computer. Screenshots are linked, not uploaded, so the tracker holds no copy of what the tested pages showed.

If the tracker refuses (a wrong or expired token, no permission, a wrong repository or project), the message says which; the token is never included.
