"""Monitors: journeys marked safe to monitor, checked on an environment; state, uptime, incidents, UI."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def call(url: str, path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(f"{url}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wait_for_check(url: str, mid: str, checks: int) -> dict:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        _, summary = call(url, f"api/monitors/{mid}")
        if not summary["busy"] and sum(1 for j in summary["journeys"] if j["checks"] >= checks) == \
                len(summary["journeys"]):
            return summary
        time.sleep(0.5)
    raise AssertionError(f"monitor {mid} did not finish check {checks}")


def test_a_monitor_checks_journeys_and_shows_uptime_and_incidents(workspace: Path, tmp_path: Path):
    (workspace / "Data" / "journeys.json").write_text(json.dumps({"journeys": [
        {"id": "search", "name": "Search the site", "system": "SMOKE", "tests": ["TC-SMOKE-001-Search"],
         "monitor_safe": True},
        {"id": "broken", "name": "Broken flow", "system": "SMOKE", "criticality": "high",
         "tests": ["TC-SMOKE-004-MissingElement"], "monitor_safe": True},
        {"id": "writes", "name": "Creates records", "system": "SMOKE", "tests": ["TC-SMOKE-002-NotesAutoGen"]},
    ]}), encoding="utf-8")
    with running_app(tmp_path) as url:
        status, body = call(url, "api/monitors", "POST", {"name": "Dev watch", "system": "SMOKE", "env": "dev",
                                                           "journeys": ["search", "writes"]})
        assert status == 400 and "not marked safe" in body
        # paused, so the background loop leaves it alone and the test decides when it checks
        status, created = call(url, "api/monitors", "POST", {
            "name": "Dev watch", "system": "SMOKE", "env": "dev", "journeys": ["search", "broken"], "every": 5,
            "enabled": False, "channels": [{"type": "webhook", "url_env": "NOTIFY_WEBHOOK_URL"}]})
        assert status == 201 and created["monitor"]["id"] == "dev-watch"
        assert [j["state"] for j in created["journeys"]] == ["never", "never"]

        status, run = call(url, "api/monitors/dev-watch/check", "POST")
        assert status == 201 and run["trigger"] == "monitor:dev-watch"
        assert sorted(run["config"]["test_ids"]) == ["TC-SMOKE-001-Search", "TC-SMOKE-004-MissingElement"]
        assert run["config"]["browsers"] == ["chromium"]
        assert call(url, "api/monitors/dev-watch/check", "POST")[0] == 409  # never two checks at once
        summary = wait_for_check(url, "dev-watch", 1)
        states = {j["id"]: j for j in summary["journeys"]}
        assert states["search"]["state"] == "up" and states["search"]["uptime"]["24h"] == 100
        assert states["broken"]["state"] == "down" and states["broken"]["uptime"]["24h"] == 0
        assert states["broken"]["where"] == "SmokeHome > missing (FILL)"
        assert summary["incidents"][0]["journey"] == "broken" and not summary["incidents"][0]["ended"]

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url, wait_until="networkidle")
            page.get_by_role("tab", name="Monitors").click()
            block = page.get_by_test_id("monitor-dev-watch")
            expect(block).to_contain_text("SMOKE/dev")
            expect(page.get_by_test_id("monitor-every-dev-watch")).to_have_text("paused")
            expect(page.get_by_test_id("monitor-state-dev-watch-search")).to_contain_text("Up")
            expect(page.get_by_test_id("monitor-state-dev-watch-broken")).to_contain_text("Down")
            expect(page.get_by_test_id("monitor-uptime-dev-watch-broken-24h")).to_have_text("0%")
            expect(page.get_by_test_id("monitor-incidents-dev-watch")).to_contain_text("ongoing")

            # a second check from the tab: the page follows it without a reload
            page.get_by_test_id("monitor-check-dev-watch").click()
            expect(page.get_by_text("Checking now")).to_be_visible()
            expect(page.get_by_test_id("monitor-uptime-dev-watch-search-24h")).to_have_text("100%")
            wait_for_check(url, "dev-watch", 2)
            expect(page.get_by_test_id("monitor-busy-dev-watch")).to_have_count(0, timeout=15000)

            # edit: turn it on every 10 minutes, from the dialog
            page.get_by_test_id("monitor-edit-dev-watch").click()
            page.get_by_test_id("mon-every").click()
            page.get_by_role("option", name="every 10 min").click()
            page.get_by_test_id("mon-enabled").click()
            page.get_by_test_id("mon-save").click()
            expect(page.get_by_text("Saved Dev watch")).to_be_visible()
            expect(page.get_by_test_id("monitor-every-dev-watch")).to_have_text("every 10 min")

            # the journey dialog has the safe-to-monitor switch
            page.get_by_role("tab", name="Journeys").click()
            page.get_by_test_id("journey-edit-writes").click()
            expect(page.get_by_test_id("jr-monitor-safe")).to_have_attribute("aria-checked", "false")
            page.keyboard.press("Escape")
            browser.close()

        saved = json.loads((workspace / "Data" / "monitors.json").read_text(encoding="utf-8"))["monitors"][0]
        assert (saved["every"], saved["enabled"], saved["journeys"]) == (10, True, ["search", "broken"])
        history = json.loads((tmp_path / "monitors" / "dev-watch.json").read_text(encoding="utf-8"))
        assert len(history["checks"]) >= 2 and len(history["incidents"]) == 1  # still down: one incident, not two
        assert call(url, "api/monitors/dev-watch", "DELETE")[0] == 200
        assert call(url, "api/monitors/dev-watch")[0] == 404
