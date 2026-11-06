"""Change impact: edit pages in a git workspace, see what it reaches in the tab, the API and the CLI; run it."""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import REPO_ROOT, running_app


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.email=qa@example.com", "-c", "user.name=QA", *args],
                   check=True, capture_output=True)


def post(url: str, path: str, body: dict):
    req = urllib.request.Request(f"{url}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_impact_of_page_edits_in_the_tab_api_and_cli(workspace: Path, tmp_path: Path):
    (workspace / "Data" / "journeys.json").write_text(json.dumps({"journeys": [
        {"id": "search", "name": "Search the site", "system": "SMOKE", "criticality": "critical",
         "tests": ["TC-SMOKE-001-Search", "TC-SMOKE-002-NotesAutoGen"]}]}), encoding="utf-8")
    git(workspace, "init", "-q")
    git(workspace, "add", ".")
    git(workspace, "commit", "-qm", "base")
    page_file = workspace / "Pages" / "SMOKE" / "SmokeHome.json"
    page = json.loads(page_file.read_text(encoding="utf-8"))
    page["fields"]["searchBox"]["description"] = "The search box"  # changed, still works
    del page["fields"]["missing"]  # removed: the test that uses it will break
    page["fields"]["email"] = {"locator": "#email"}  # new, nothing uses it yet
    page_file.write_text(json.dumps(page, indent=2), encoding="utf-8")

    with running_app(tmp_path) as url:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page_ = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_.goto(url, wait_until="networkidle")
            page_.get_by_role("tab", name="Impact").click()
            page_.get_by_test_id("impact-analyze").click()
            summary = page_.get_by_test_id("impact-summary")
            expect(summary).to_contain_text("3 changes")
            expect(summary).to_contain_text("2 test cases reached")
            expect(summary).to_contain_text("1 will break")
            expect(summary).to_contain_text("1 not tested")
            broken = page_.get_by_test_id("impact-test-TC-SMOKE-004-MissingElement")
            expect(broken).to_contain_text("will break")
            expect(broken).to_contain_text("uses SmokeHome > missing, which was removed")
            expect(page_.get_by_test_id("impact-test-TC-SMOKE-001-Search")).to_contain_text("Search the site")
            expect(page_.get_by_test_id("impact-journeys")).to_contain_text("1 of its 2 test cases")
            expect(page_.get_by_test_id("impact-changes")).to_contain_text("SmokeHome > email (added)")
            expect(page_.get_by_test_id("impact-changes").locator("tr", has_text="email")) \
                .to_contain_text("not tested")

            # the screens the developers changed, without git
            page_.get_by_test_id("impact-base").fill("")
            page_.get_by_test_id("impact-screens").click()
            page_.get_by_role("option", name="SmokeHome").click()
            page_.keyboard.press("Escape")
            page_.get_by_test_id("impact-analyze").click()
            expect(page_.get_by_test_id("impact-summary")).to_contain_text("4 test cases reached")

            page_.get_by_test_id("impact-base").fill("no-such-branch")
            page_.get_by_test_id("impact-analyze").click()
            expect(page_.get_by_test_id("impact-error")).to_be_visible()

            page_.get_by_test_id("impact-base").fill("HEAD")
            page_.get_by_test_id("impact-screens").click()
            page_.get_by_role("option", name="SmokeHome").click()  # unpick it
            page_.keyboard.press("Escape")
            page_.get_by_test_id("impact-analyze").click()
            page_.get_by_test_id("impact-run").click()
            expect(page_.get_by_text("Running the impacted test cases on dev")).to_be_visible()
            expect(page_.get_by_role("tab", name="Control")).to_have_attribute("aria-selected", "true")
            browser.close()

        status, report = post(url, "api/impact", {"base": "HEAD"})
        assert status == 200
        assert [t["test_id"] for t in report["tests"]] == ["TC-SMOKE-004-MissingElement", "TC-SMOKE-001-Search"]
        assert report["selection"] == {"SMOKE": ["TC-SMOKE-004-MissingElement", "TC-SMOKE-001-Search"]}
        assert [c["field"] for c in report["not_covered"]] == ["email"]
        assert post(url, "api/impact", {"base": "nope"})[0] == 400
        assert post(url, "api/impact", {})[0] == 400
        assert post(url, "api/impact/run", {"base": "HEAD"})[0] == 400  # no environment
        status, started = post(url, "api/impact/run", {"base": "HEAD", "env": "dev"})
        assert status == 201 and started["runs"][0]["trigger"] == "impact"
        assert started["runs"][0]["config"]["test_ids"] == ["TC-SMOKE-004-MissingElement", "TC-SMOKE-001-Search"]

    cli = subprocess.run([sys.executable, "-m", "webapp.impact", "--base", "HEAD", "--workspace", str(workspace),
                          "--fail-on-breaks"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert cli.returncode == 3, cli.stderr
    assert "BREAKS SMOKE/TC-SMOKE-004-MissingElement" in cli.stdout and "not tested: SmokeHome > email" in cli.stdout
    as_json = subprocess.run([sys.executable, "-m", "webapp.impact", "--base", "HEAD", "--workspace",
                              str(workspace), "--json"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert as_json.returncode == 0 and json.loads(as_json.stdout)["journeys"][0]["id"] == "search"
