"""File a bug for a failed test in GitHub Issues or Jira, with the failure already written up.

Configure it in the workspace .env (or the environment); nothing is stored in the app:

    ISSUE_TRACKER=github                      # or jira
    ISSUE_GITHUB_REPO=owner/repo              ISSUE_GITHUB_TOKEN=...      (needs permission to create issues)
    ISSUE_GITHUB_LABELS=bug,autoplay          ISSUE_GITHUB_API_URL=https://api.github.com  (GitHub Enterprise: yours)
    ISSUE_JIRA_URL=https://yourcompany.atlassian.net   ISSUE_JIRA_PROJECT=QA   ISSUE_JIRA_TYPE=Bug
    ISSUE_JIRA_USER=you@company.com  ISSUE_JIRA_TOKEN=...   (leave the user empty for a Jira Server personal token)

The report links use AUTOPLAY_PUBLIC_URL, so they open for the people who read the issue. Issues filed from a run are
remembered in its folder (issues.json) so the same failure is not filed twice by accident.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

TRACE_LINES = 25
TIMEOUT = 30


class IssueError(Exception):
    """Shown to the user as is (never contains a token)."""


@dataclass
class Issue:
    title: str
    markdown: str  # GitHub
    wiki: str  # Jira


@dataclass
class Filed:
    tracker: str
    key: str  # "#12" or "QA-34"
    url: str


def _clip(text: str, lines: int) -> str:
    parts = (text or "").strip().splitlines()
    return "\n".join(parts[:lines]) + (f"\n... ({len(parts) - lines} more lines)" if len(parts) > lines else "")


def build_issue(run, result, details, public_url: str) -> Issue:
    """The write-up of one failed test result. `details` is a TestDetails or None."""
    point = details.failure if details else None
    where = point.short if point else ""
    title = f"[AutoPlay] {result.test_id} {result.outcome}" + (f" at {where}" if where else "")
    base = public_url.rstrip("/")
    report = f"{base}/runs/{run.id}/report.html"
    rows = [("Test case", result.test_id), ("Run", f"{run.id} ({run.status})"),
            ("System / environment", f"{run.config.system} / {run.config.env}"),
            ("Browser", result.browser or "-"), ("Result", result.outcome)]
    if result.retries:
        rows.append(("Retries", f"{result.retries} (flaky if it passed on retry)"))
    if where:
        rows.append(("Failed at", where))
    message = (details.message if details and details.message else result.message) or ""
    trace = _clip(details.trace, TRACE_LINES) if details and details.trace else ""
    shots = [f"{base}{a.url}" for a in (details.by_kind("image") if details else [])][:3]
    reproduce = (f"Run `{result.test_id}` on {run.config.system}/{run.config.env} "
                 f"(tags: {', '.join(run.config.tags)}).")

    md = ["| | |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows], ""]
    wiki = [f"||{k}|{v}|" for k, v in rows]
    if message:
        md += ["**Error**", "```", message.strip(), "```", ""]
        wiki += ["", "*Error*", "{code}", message.strip(), "{code}"]
    if trace:
        md += ["<details><summary>Traceback</summary>", "", "```", trace, "```", "", "</details>", ""]
        wiki += ["", "*Traceback*", "{code}", trace, "{code}"]
    md += [f"[Run report]({report})"] + [f"[Screenshot {i}]({u})" for i, u in enumerate(shots, 1)]
    wiki += ["", f"[Run report|{report}]"] + [f"[Screenshot {i}|{u}]" for i, u in enumerate(shots, 1)]
    md += ["", reproduce]
    wiki += ["", reproduce.replace("`", "")]
    return Issue(title[:200], "\n".join(md), "\n".join(wiki))


class IssueTracker:
    def __init__(self, lookup: Callable[[str], str | None]):
        self.get = lambda key: (lookup(key) or "").strip()

    @property
    def kind(self) -> str:
        return self.get("ISSUE_TRACKER").lower()

    def blocker(self) -> str:
        """Why filing is not possible ("" = it is)."""
        kind = self.kind
        if not kind:
            return "No issue tracker is set up (ISSUE_TRACKER in .env)."
        if kind == "github":
            missing = [k for k in ("ISSUE_GITHUB_REPO", "ISSUE_GITHUB_TOKEN") if not self.get(k)]
            if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", self.get("ISSUE_GITHUB_REPO") or "x/y"):
                return "ISSUE_GITHUB_REPO must look like owner/repo."
        elif kind == "jira":
            missing = [k for k in ("ISSUE_JIRA_URL", "ISSUE_JIRA_PROJECT", "ISSUE_JIRA_TOKEN") if not self.get(k)]
        else:
            return f"ISSUE_TRACKER must be github or jira, not {kind!r}."
        if missing:
            return f"Set {', '.join(missing)} in .env to file issues."
        return ""

    # ───── remembered issues ─────

    @staticmethod
    def _memory(run) -> Path:
        return Path(run.dir) / "issues.json"

    def existing(self, run, test_id: str, browser: str) -> Filed | None:
        try:
            item = json.loads(self._memory(run).read_text(encoding="utf-8")).get(f"{test_id}|{browser}")
        except (OSError, ValueError):
            return None
        return Filed(item["tracker"], item["key"], item["url"]) if isinstance(item, dict) else None

    def _remember(self, run, test_id: str, browser: str, filed: Filed) -> None:
        try:
            data = json.loads(self._memory(run).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[f"{test_id}|{browser}"] = {"tracker": filed.tracker, "key": filed.key, "url": filed.url,
                                        "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._memory(run).write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ───── filing ─────

    def file(self, run, test_id: str, browser: str, issue: Issue) -> Filed:
        reason = self.blocker()
        if reason:
            raise IssueError(reason)
        filed = self._github(issue) if self.kind == "github" else self._jira(issue)
        self._remember(run, test_id, browser, filed)
        return filed

    def _github(self, issue: Issue) -> Filed:
        api = (self.get("ISSUE_GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        repo = self.get("ISSUE_GITHUB_REPO")
        body: dict = {"title": issue.title, "body": issue.markdown}
        labels = [x.strip() for x in self.get("ISSUE_GITHUB_LABELS").split(",") if x.strip()]
        if labels:
            body["labels"] = labels
        data = self._post(f"{api}/repos/{quote(repo, safe='/')}/issues", body, {
            "Authorization": f"Bearer {self.get('ISSUE_GITHUB_TOKEN')}", "Accept": "application/vnd.github+json"})
        return Filed("github", f"#{data['number']}", str(data["html_url"]))

    def _jira(self, issue: Issue) -> Filed:
        base = self.get("ISSUE_JIRA_URL").rstrip("/")
        user, token = self.get("ISSUE_JIRA_USER"), self.get("ISSUE_JIRA_TOKEN")
        auth = ("Basic " + base64.b64encode(f"{user}:{token}".encode()).decode()) if user else f"Bearer {token}"
        data = self._post(f"{base}/rest/api/2/issue", {"fields": {
            "project": {"key": self.get("ISSUE_JIRA_PROJECT")}, "summary": issue.title, "description": issue.wiki,
            "issuetype": {"name": self.get("ISSUE_JIRA_TYPE") or "Bug"}}}, {"Authorization": auth})
        return Filed("jira", str(data["key"]), f"{base}/browse/{quote(str(data['key']))}")

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise IssueError("The issue tracker address must be a full http(s) URL.")
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "AutoPlay", **headers})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            hint = {401: " (the token was refused)", 403: " (the token may lack permission to create issues)",
                    404: " (check the repository or project name)"}.get(e.code, "")
            raise IssueError(f"The issue tracker answered {e.code}{hint}: {detail}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise IssueError(f"Could not reach the issue tracker: {getattr(e, 'reason', e)}") from None
