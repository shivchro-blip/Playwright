"""UI smoke: every tab, a run from Control, a test's details, the built-in report, a Generator locator check."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.e2e.conftest import free_port
from tests.e2e.fixtures.ai import fake_anthropic

REPO_ROOT = Path(__file__).resolve().parents[2]


@contextlib.contextmanager
def running_app(tmp_path: Path, **extra_env: str):
    env_file = tmp_path / "ui.env"
    env_file.write_text("systemConfig=SMOKE\nenvConfig=dev\nrunTags=smoke\nbrowsers=chromium\nheadlessMode=true\n",
                        encoding="utf-8")
    port = free_port()
    # Drop PYTEST_* variables: NiceGUI treats them as its own screen-test mode
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    fake_codegen = Path(__file__).resolve().parent / "fixtures" / "recorder" / "fake_codegen.py"
    base_env = {k: v for k, v in base_env.items() if k not in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")}
    ai_server, ai_url = fake_anthropic.serve()  # AI assist talks to a local fake Messages API
    env = {**base_env, "AUTOMATENY_PORT": str(port), "AUTOMATENY_RUNS_DIR": str(tmp_path / "runs"),
           "AUTOMATENY_DOTENV_PATH": str(env_file), "PYTHONUNBUFFERED": "1",
           "AUTOMATENY_RECORDER_COMMAND": json.dumps([sys.executable, str(fake_codegen)]),
           "ANTHROPIC_BASE_URL": ai_url, "ANTHROPIC_API_KEY": "sk-ant-test-not-a-real-key", **extra_env}
    log = (tmp_path / "webapp.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "webapp"], cwd=REPO_ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "login", timeout=2)  # public, with or without sign-in
                break
            except OSError:
                if proc.poll() is not None:
                    pytest.fail("web app exited:\n" + (tmp_path / "webapp.log").read_text(encoding="utf-8"))
                time.sleep(0.5)
        else:
            pytest.fail("web app did not start")
        yield url
    finally:
        ai_server.shutdown()
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        server_log = (tmp_path / "webapp.log").read_text(encoding="utf-8")
        assert "Traceback" not in server_log, server_log


@pytest.fixture
def app_url(workspace: Path, tmp_path: Path):
    with running_app(tmp_path) as url:
        yield url


def test_every_tab_loads_and_a_run_passes(app_url: str):
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: m.type in ("error", "warning") and problems.append(m.text))
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(app_url, wait_until="networkidle")

        for tab in ("Generator", "History", "Help", "Control"):
            page.get_by_role("tab", name=tab).click()
            expect(page.get_by_role("tab", name=tab)).to_have_attribute("aria-current", "page")
        expect(page.get_by_role("tabpanel").get_by_text("Login credentials")).to_be_visible()

        expect(page.get_by_test_id("run-formula")).to_contain_text("2 tests")
        page.get_by_test_id("run-button").click()
        expect(page.locator(".any-headline")).to_have_text("Passed", timeout=180_000)
        run_card = page.locator("[aria-labelledby='current-run-title']")
        expect(run_card.locator("tbody .pill-passed")).to_have_count(2)  # both smoke tests, in the results table
        expect(run_card.get_by_test_id("duration-bar")).to_be_visible()

        run_card.locator("tbody tr").first.click()  # failure details dialog (a passed test still has its log)
        details = page.get_by_role("dialog", name="Test details")
        expect(details.get_by_text("Test log")).to_be_visible()
        details.get_by_role("button", name="Close").click()

        with page.context.expect_page() as report_page:  # built-in report, no Allure CLI needed
            run_card.get_by_test_id("run-report").click()
        expect(report_page.value.get_by_text("AutomateNY run report")).to_be_visible()
        report_page.value.close()

        page.get_by_role("tab", name="Generator").click()  # live locator check on the open page's first field
        page.get_by_role("button", name="Check locator on the live page").first.click()
        check = page.get_by_role("dialog", name="Check locator")
        check.get_by_role("button", name="Check").click()
        expect(check.get_by_test_id("lc-count")).to_have_text("1 match", timeout=60_000)
        expect(check.locator("figure img")).to_be_visible()
        browser.close()
    assert problems == []


def test_recorder_is_off_by_default_and_records_a_test(app_url: str, tmp_path: Path):
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(app_url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        switch = page.get_by_test_id("recorder-switch")
        expect(switch).to_have_attribute("aria-checked", "false")
        expect(page.get_by_test_id("record-button")).to_be_hidden()

        switch.click()
        expect(page.get_by_test_id("record-button")).to_be_visible()
        assert "recorderEnabled=true" in (tmp_path / "ui.env").read_text(encoding="utf-8")

        page.get_by_test_id("record-button").click()
        dialog = page.get_by_role("dialog", name="Record a test")
        dialog.get_by_test_id("rec-start").click()  # the fake codegen records three fields and exits
        expect(dialog.get_by_text("3 fields")).to_be_visible(timeout=30_000)
        dialog.get_by_label("Test case ID").fill("TC-SMOKE-REC-UI")
        dialog.get_by_test_id("rec-save").click()
        expect(dialog).to_be_hidden()
        expect(page.get_by_role("tab", name="Test cases")).to_have_attribute("aria-selected", "true")
        expect(page.get_by_text("TC-SMOKE-REC-UI").first).to_be_visible()

        switch.click()  # and off again
        expect(page.get_by_test_id("record-button")).to_be_hidden()
        browser.close()
    assert "recorderEnabled=false" in (tmp_path / "ui.env").read_text(encoding="utf-8")
    assert problems == []


def test_schedule_is_created_and_runs_now(app_url: str):
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(app_url, wait_until="networkidle")
        page.get_by_role("tab", name="Schedules").click()
        page.get_by_test_id("new-schedule").click()
        dialog = page.get_by_role("dialog", name="New schedule")
        dialog.get_by_label("Name").fill("UI smoke")
        dialog.locator("#sch-tags").click()
        page.keyboard.type("smoke")
        page.keyboard.press("Enter")
        page.keyboard.press("Escape")
        expect(dialog.get_by_text("2 test cases match these tags.")).to_be_visible()
        expect(dialog.get_by_test_id("sch-next")).to_contain_text("On weekdays at 06:30")
        dialog.get_by_test_id("sch-save").click()
        expect(dialog).to_be_hidden()
        row = page.get_by_role("region", name="Schedules").locator("tbody tr").filter(has_text="UI smoke")
        expect(row).to_contain_text("On weekdays at 06:30")
        row.get_by_role("button", name="Run UI smoke now").click()
        expect(page.locator(".any-headline")).to_have_text("Passed", timeout=180_000)
        page.get_by_role("tab", name="History").click()
        expect(page.get_by_role("region", name="Runs").get_by_text("scheduled").first).to_be_visible()
        browser.close()
    assert problems == []


def test_ai_assist_is_opt_in_and_only_suggests(app_url: str, tmp_path: Path):
    problems: list[str] = []
    urllib.request.urlopen(urllib.request.Request(  # a failing run (unknown autoGen rule) to triage
        app_url + "api/runs", method="POST", headers={"Content-Type": "application/json"},
        data=json.dumps({"system": "SMOKE", "env": "dev", "tags": ["fail"], "browsers": ["chromium"],
                         "options": {"headlessMode": True}}).encode()), timeout=10)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(app_url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        expect(page.get_by_test_id("ai-draft-open")).to_be_hidden()  # off by default

        page.get_by_test_id("ai-settings").click()
        settings = page.get_by_role("dialog", name="AI assist settings")
        expect(settings.get_by_text("ANTHROPIC_API_KEY is set")).to_be_visible()
        settings.get_by_test_id("ai-switch").click()
        settings.get_by_role("button", name="Close").click()
        assert "aiAssistEnabled=true" in (tmp_path / "ui.env").read_text(encoding="utf-8")

        page.get_by_role("tab", name="History").click()
        runs = page.get_by_role("region", name="Runs")
        expect(runs.locator("tbody .pill-failed")).to_have_count(1, timeout=180_000)
        runs.locator("tbody tr").first.click()
        details = page.get_by_role("region", name="Run details")
        details.locator("tbody tr").filter(has_text="BadAutoGen").click()
        test_dialog = page.get_by_role("dialog", name="Test details")
        test_dialog.get_by_test_id("ai-triage").click()
        expect(test_dialog.get_by_test_id("ai-category")).to_have_text("Locator changed")
        test_dialog.get_by_role("button", name="Close").click()

        details.get_by_test_id("ai-summary").click()
        summary = page.get_by_role("dialog", name="Run summary")
        expect(summary.get_by_test_id("ai-summary-headline")).to_have_text("All smoke tests passed.")
        summary.get_by_role("button", name="Close").click()

        page.get_by_role("tab", name="Generator").click()
        page.get_by_role("tab", name="Test cases").click()
        page.get_by_test_id("ai-draft-open").click()
        draft = page.get_by_role("dialog", name="Draft a test with AI")
        draft.get_by_test_id("ai-draft-input").fill("Search for shoes")
        draft.get_by_test_id("ai-draft").click()
        expect(draft.get_by_test_id("ai-draft-json")).to_contain_text("TC-SMOKE-AI-Search")
        draft.get_by_test_id("ai-draft-create").click()
        page.get_by_role("button", name="Create", exact=True).click()  # the usual file dialog: nothing saved before
        expect(page.get_by_role("region", name="Test cases").get_by_text("TC-SMOKE-AI-Search").first).to_be_visible()
        browser.close()
    assert problems == []


def test_sign_in_roles_and_activity(workspace: Path, tmp_path: Path):
    from webapp.services.users import UserStore
    users = UserStore(tmp_path / "users.json")
    users.add("ann", "admin password 1", "admin")
    users.add("vic", "viewer password 1", "viewer")
    problems: list[str] = []
    with running_app(tmp_path, AUTOMATENY_AUTH_ENABLED="true", AUTOMATENY_USERS_FILE=str(users.path)) as url:
        assert urllib.request.urlopen(url + "login").status == 200
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.goto(url + "history")
            expect(page).to_have_url(url + "login?next=/history")
            page.get_by_label("User name").fill("vic")
            page.get_by_label("Password").fill("wrong password")
            page.get_by_role("button", name="Sign in").click()
            expect(page.get_by_role("alert")).to_have_text("User name or password is wrong.")
            page.get_by_label("User name").fill("vic")
            page.get_by_label("Password").fill("viewer password 1")
            page.get_by_role("button", name="Sign in").click()
            page.goto(url)
            expect(page.get_by_test_id("signed-in")).to_contain_text("vic")
            expect(page.get_by_test_id("activity")).to_have_count(0)  # admins only
            page.get_by_test_id("run-button").click()  # viewers can look, not run
            expect(page.get_by_text("Your role (viewer) can't start or cancel runs.")).to_be_visible()
            page.get_by_text("Sign out").click()
            expect(page).to_have_url(url + "login")

            page.get_by_label("User name").fill("ann")
            page.get_by_label("Password").fill("admin password 1")
            page.get_by_role("button", name="Sign in").click()
            expect(page.get_by_test_id("signed-in")).to_contain_text("admin")
            page.get_by_test_id("activity").click()
            activity = page.get_by_role("dialog", name="Activity")
            expect(activity.get_by_test_id("activity-table")).to_contain_text("login.failed")
            expect(activity.get_by_test_id("activity-table")).to_contain_text("denied")
            browser.close()
    assert problems == []
