"""Automations end to end: a rule made in the tab fires on a deploy announced to the webhook, and a "run finished"
rule never sets itself off through the run it starts."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def api(url: str, path: str, body: dict | None = None, method: str | None = None):
    req = urllib.request.Request(url + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def finished_runs(url: str, trigger: str) -> list[dict]:
    return [r for r in api(url, f"api/v1/runs?trigger={trigger}") if r["status"] not in ("queued", "running")]


def wait_for(check, seconds: float = 240):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(1)
    raise AssertionError("timed out")


def test_a_rule_fires_on_a_deploy_and_never_loops(workspace: Path, tmp_path: Path):
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(f"{url}#automations", wait_until="networkidle")
        expect(page.get_by_test_id("automation-empty")).to_be_visible()

        page.get_by_test_id("automation-new").click()
        dialog = page.get_by_test_id("automation-dialog")
        dialog.get_by_test_id("auto-name").fill("Smoke after deploy")
        dialog.get_by_test_id("auto-env").fill("dev")
        dialog.get_by_test_id("auto-do-tests").click()
        dialog.get_by_test_id("auto-tags").fill("smoke")
        dialog.get_by_test_id("auto-save").click()
        rule = page.locator("[data-rule=smoke-after-deploy]")
        expect(rule).to_contain_text("When a deploy is announced on any system/dev: run tests tagged smoke")

        fired = api(url, "api/v1/automations/deploy", {"system": "SMOKE", "env": "dev", "build": "482", "by": "ci"})
        assert fired["rules"] == ["smoke-after-deploy"]
        [run] = wait_for(lambda: finished_runs(url, "automation:smoke-after-deploy"))
        assert run["status"] == "passed" and run["config"]["tags"] == ["smoke"] and run["config"]["env"] == "dev"
        expect(page.get_by_test_id("automation-recent")).to_contain_text("Smoke after deploy")
        expect(page.get_by_test_id("automation-recent")).to_contain_text("482")

        # a rule on every finished run that starts a run: the run it starts must not set it off again
        api(url, "api/v1/automations/again", {"name": "Again after any run", "when": {"on": "run_finished"},
                                               "do": [{"type": "run_tests", "tags": ["smoke"]}]}, method="PUT")
        api(url, "api/v1/runs", {"system": "SMOKE", "env": "dev", "tags": ["smoke"], "browsers": ["chromium"],
                                 "options": {"headlessMode": True}})
        [again] = wait_for(lambda: finished_runs(url, "automation:again"))
        assert again["status"] == "passed"
        time.sleep(5)  # long enough for a loop to have started another run
        assert len(api(url, "api/v1/runs?trigger=automation:again")) == 1
        recent = api(url, "api/v1/automations")["recent"]
        assert [r["rule"] for r in recent if r["rule"] == "again"] == ["again"]
        browser.close()
    assert problems == []
