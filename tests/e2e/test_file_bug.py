"""File a bug from the failure dialog: preview, send to a (fake) tracker, remember it, refuse when not set up."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e import fake_trackers
from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app


def open_failure(page, url):
    page.goto(url, wait_until="networkidle")
    page.locator("tbody tr", has_text="MissingElement").first.click()
    return page.get_by_role("dialog", name="Test details")


def test_the_button_is_hidden_until_a_tracker_is_set_up(workspace: Path, tmp_path: Path):
    run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True})
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        dialog = open_failure(page, url)
        expect(dialog.get_by_test_id("failed-at")).to_be_visible()
        expect(dialog.get_by_test_id("file-bug")).to_have_count(0)
        browser.close()


def test_file_a_bug_on_github_with_a_preview_and_no_duplicates(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True})
    server, base, seen = fake_trackers.serve()
    try:
        with running_app(tmp_path, ISSUE_TRACKER="github", ISSUE_GITHUB_REPO="acme/portal",
                         ISSUE_GITHUB_TOKEN="ghp_test", ISSUE_GITHUB_API_URL=base,
                         ISSUE_GITHUB_LABELS="bug") as url, sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            dialog = open_failure(page, url)
            dialog.get_by_test_id("file-bug").click()
            bug = page.get_by_role("dialog", name="File a bug")
            expect(bug.get_by_test_id("bug-title")).to_have_value(
                "[AutoPlay] TC-SMOKE-004-MissingElement failed at SmokeHome > missing (FILL)")
            expect(bug.get_by_test_id("bug-existing")).to_have_count(0)
            body = bug.get_by_test_id("bug-body")
            assert "Failed at | SmokeHome > missing (FILL)" in body.input_value()
            assert f"/runs/{run.id}/report.html" in body.input_value()
            body.fill(body.input_value() + "\nSeen on the nightly build.")
            bug.get_by_test_id("bug-file").click()
            expect(page.get_by_text("Filed #")).to_be_visible()

            dialog = open_failure(page, url)  # a fresh page: the same failure again
            dialog.get_by_test_id("file-bug").click()
            again = page.get_by_role("dialog", name="File a bug")
            expect(again.get_by_test_id("bug-existing")).to_contain_text("#")
            expect(again.get_by_test_id("bug-file")).to_have_text("File another")
            browser.close()
    finally:
        server.shutdown()
    assert len(seen) == 1
    path, headers, sent = seen[0]
    assert path == "/repos/acme/portal/issues" and headers["authorization"] == "Bearer ghp_test"
    assert sent["labels"] == ["bug"] and sent["body"].rstrip().endswith("Seen on the nightly build.")
    assert (run.dir / "issues.json").is_file()


def test_a_tracker_error_is_shown_and_nothing_is_remembered(workspace: Path, tmp_path: Path):
    run = run_and_wait(workspace, tmp_path, tags=["missing"], options={"headlessMode": True})
    server, base, _ = fake_trackers.serve(fail_with=401)
    try:
        with running_app(tmp_path, ISSUE_TRACKER="jira", ISSUE_JIRA_URL=base, ISSUE_JIRA_PROJECT="QA",
                         ISSUE_JIRA_TOKEN="pat") as url, sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            dialog = open_failure(page, url)
            dialog.get_by_test_id("file-bug").click()
            bug = page.get_by_role("dialog", name="File a bug")
            expect(bug.get_by_role("heading", name="File a bug in Jira")).to_be_visible()
            bug.get_by_test_id("bug-file").click()
            expect(page.get_by_text("the token was refused")).to_be_visible()
            browser.close()
    finally:
        server.shutdown()
    assert not (run.dir / "issues.json").exists()
