"""UI smoke: start the web app, open every tab, start a run from the Control page and watch it pass."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.e2e.conftest import free_port

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def app_url(workspace: Path, tmp_path: Path):
    env_file = tmp_path / "ui.env"
    env_file.write_text("systemConfig=SMOKE\nenvConfig=dev\nrunTags=smoke\nbrowsers=chromium\nheadlessMode=true\n",
                        encoding="utf-8")
    port = free_port()
    # Drop PYTEST_* variables: NiceGUI treats them as its own screen-test mode
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env = {**base_env, "AUTOMATENY_PORT": str(port), "AUTOMATENY_RUNS_DIR": str(tmp_path / "runs"),
           "AUTOMATENY_DOTENV_PATH": str(env_file), "PYTHONUNBUFFERED": "1"}
    log = (tmp_path / "webapp.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "webapp"], cwd=REPO_ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "api/systems", timeout=2)
                break
            except OSError:
                if proc.poll() is not None:
                    pytest.fail("web app exited:\n" + (tmp_path / "webapp.log").read_text(encoding="utf-8"))
                time.sleep(0.5)
        else:
            pytest.fail("web app did not start")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        server_log = (tmp_path / "webapp.log").read_text(encoding="utf-8")
        assert "Traceback" not in server_log, server_log


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
        browser.close()
    assert problems == []
