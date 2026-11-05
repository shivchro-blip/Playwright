"""Filing a bug: the write-up, the GitHub and Jira calls, the refusals, and remembering what was filed."""
from __future__ import annotations

import base64
import pytest

from tests.e2e import fake_trackers
from webapp.services.issue_tracker import IssueError, IssueTracker, build_issue
from webapp.services.run_artifacts import Attachment, FailurePoint, TestDetails
from webapp.services.run_manager import Run, RunConfig, TestResult


def run_and_result(tmp_path):
    run = Run(id="20261105-100000-ab12", config=RunConfig(system="S", env="dev", tags=["smoke", "wcb"]),
              dir=tmp_path, status="failed")
    result = TestResult("n", "TC-1", "chromium", "failed", 3.0, "boom", retries=1)
    point = FailurePoint(screen="Applicant", action="FILL", field_name="Zip", step_no=4)
    details = TestDetails("TC-1", "chromium", "failed", message="Locator expected to be visible",
                          trace="\n".join(f"line {i}" for i in range(40)), failure=point,
                          attachments=[Attachment("shot", "/runs/20261105-100000-ab12/allure-results/x-attachment.png",
                                                  "image", 10)])
    return run, result, details


def lookup(**values):
    return lambda key: values.get(key)


def test_the_write_up_has_the_failure_the_run_and_links(tmp_path):
    run, result, details = run_and_result(tmp_path)
    issue = build_issue(run, result, details, "https://autoplay.example.com/")
    assert issue.title == "[AutoPlay] TC-1 failed at Applicant > Zip (FILL)"
    for needle in ("| Test case | TC-1 |", "S / dev", "Retries", "Locator expected to be visible", "line 24",
                   "(15 more lines)", "https://autoplay.example.com/runs/20261105-100000-ab12/report.html",
                   "Screenshot 1", "tags: smoke, wcb"):
        assert needle in issue.markdown, needle
    assert "line 25" not in issue.markdown
    assert "||Test case|TC-1|" in issue.wiki and "{code}" in issue.wiki and "[Run report|https://" in issue.wiki
    bare = build_issue(run, result, None, "http://127.0.0.1:8080")
    assert bare.title == "[AutoPlay] TC-1 failed" and "boom" in bare.markdown


@pytest.mark.parametrize("values, expected", [
    ({}, "No issue tracker"),
    ({"ISSUE_TRACKER": "trello"}, "github or jira"),
    ({"ISSUE_TRACKER": "github"}, "ISSUE_GITHUB_REPO, ISSUE_GITHUB_TOKEN"),
    ({"ISSUE_TRACKER": "github", "ISSUE_GITHUB_REPO": "not a repo", "ISSUE_GITHUB_TOKEN": "t"}, "owner/repo"),
    ({"ISSUE_TRACKER": "jira", "ISSUE_JIRA_URL": "https://j"}, "ISSUE_JIRA_PROJECT, ISSUE_JIRA_TOKEN"),
])
def test_setup_problems_are_named(values, expected):
    assert expected in IssueTracker(lookup(**values)).blocker()


def test_github_issue_is_created_and_remembered(tmp_path):
    server, base, seen = fake_trackers.serve()
    try:
        run, result, details = run_and_result(tmp_path)
        tracker = IssueTracker(lookup(ISSUE_TRACKER="github", ISSUE_GITHUB_REPO="acme/portal",
                                      ISSUE_GITHUB_TOKEN="ghp_secret", ISSUE_GITHUB_API_URL=base,
                                      ISSUE_GITHUB_LABELS="bug, autoplay"))
        assert tracker.blocker() == "" and tracker.existing(run, "TC-1", "chromium") is None
        filed = tracker.file(run, "TC-1", "chromium", build_issue(run, result, details, "http://x"))
    finally:
        server.shutdown()
    path, headers, body = seen[0]
    assert path == "/repos/acme/portal/issues" and headers["authorization"] == "Bearer ghp_secret"
    assert body["labels"] == ["bug", "autoplay"] and body["title"].startswith("[AutoPlay] TC-1")
    assert filed.tracker == "github" and filed.key == "#42" and filed.url.endswith("/issues/42")
    again = tracker.existing(run, "TC-1", "chromium")
    assert (again.key, again.url) == (filed.key, filed.url) and tracker.existing(run, "TC-2", "chromium") is None


def test_jira_issue_uses_basic_auth_with_a_user_and_bearer_without(tmp_path):
    server, base, seen = fake_trackers.serve()
    try:
        run, result, details = run_and_result(tmp_path)
        cloud = IssueTracker(lookup(ISSUE_TRACKER="jira", ISSUE_JIRA_URL=base + "/", ISSUE_JIRA_PROJECT="QA",
                                    ISSUE_JIRA_USER="me@acme.com", ISSUE_JIRA_TOKEN="tok"))
        filed = cloud.file(run, "TC-1", "chromium", build_issue(run, result, details, "http://x"))
        pat = IssueTracker(lookup(ISSUE_TRACKER="jira", ISSUE_JIRA_URL=base, ISSUE_JIRA_PROJECT="QA",
                                  ISSUE_JIRA_TOKEN="pat", ISSUE_JIRA_TYPE="Task"))
        pat.file(run, "TC-9", "chromium", build_issue(run, result, details, "http://x"))
    finally:
        server.shutdown()
    (path, headers, body), (_, headers2, body2) = seen
    assert path == "/rest/api/2/issue" and body["fields"]["project"] == {"key": "QA"}
    assert body["fields"]["issuetype"] == {"name": "Bug"} and "{code}" in body["fields"]["description"]
    assert headers["authorization"] == "Basic " + base64.b64encode(b"me@acme.com:tok").decode()
    assert headers2["authorization"] == "Bearer pat" and body2["fields"]["issuetype"] == {"name": "Task"}
    assert filed.key.startswith("QA-") and filed.url == f"{base}/browse/{filed.key}"


@pytest.mark.parametrize("status, hint", [(401, "token was refused"), (403, "permission"),
                                          (404, "repository or project")])
def test_tracker_errors_are_explained_without_the_token(tmp_path, status, hint):
    server, base, _ = fake_trackers.serve(fail_with=status)
    try:
        run, result, details = run_and_result(tmp_path)
        tracker = IssueTracker(lookup(ISSUE_TRACKER="github", ISSUE_GITHUB_REPO="a/b", ISSUE_GITHUB_TOKEN="ghp_secret",
                                      ISSUE_GITHUB_API_URL=base))
        with pytest.raises(IssueError, match=hint) as err:
            tracker.file(run, "TC-1", "chromium", build_issue(run, result, details, "http://x"))
    finally:
        server.shutdown()
    assert "ghp_secret" not in str(err.value) and tracker.existing(run, "TC-1", "chromium") is None


def test_an_unreachable_or_odd_address_is_reported(tmp_path):
    run, result, details = run_and_result(tmp_path)
    issue = build_issue(run, result, details, "http://x")
    down = IssueTracker(lookup(ISSUE_TRACKER="github", ISSUE_GITHUB_REPO="a/b", ISSUE_GITHUB_TOKEN="t",
                               ISSUE_GITHUB_API_URL="http://127.0.0.1:1"))
    with pytest.raises(IssueError, match="Could not reach"):
        down.file(run, "TC-1", "chromium", issue)
    odd = IssueTracker(lookup(ISSUE_TRACKER="jira", ISSUE_JIRA_URL="file:///etc", ISSUE_JIRA_PROJECT="Q",
                              ISSUE_JIRA_TOKEN="t"))
    with pytest.raises(IssueError, match="http"):
        odd.file(run, "TC-1", "chromium", issue)
