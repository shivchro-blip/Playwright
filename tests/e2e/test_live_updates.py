"""Pages update when something changes (the event bus), and the API streams the same events."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def call(url: str, body: dict | None = None):
    req = urllib.request.Request(url, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def test_a_run_is_pushed_to_the_pages_and_the_events_api(workspace: Path, tmp_path: Path):
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="History").click()
        runs = page.get_by_role("region", name="Runs")
        expect(runs.locator("tbody tr")).to_have_count(0)

        start = call(f"{url}api/events")["seq"]
        run = call(f"{url}api/runs", {"system": "SMOKE", "env": "dev", "tags": ["smoke"], "browsers": ["chromium"],
                                      "options": {"headlessMode": True}})
        run_id = run["id"]
        # History's fallback timer is 30 s: a row this quickly came from the event
        expect(runs.locator("tbody tr")).to_have_count(1, timeout=10_000)

        changes, seq, deadline = [], start, time.monotonic() + 240
        while "finished" not in changes and time.monotonic() < deadline:
            got = call(f"{url}api/events?since={seq}&topics=run&wait=20")
            assert got["seq"] >= seq
            seq = got["seq"]
            changes += [e["change"] for e in got["events"] if e.get("id") == run_id]
        assert "finished" in changes, changes
        assert changes[0] == "saved" and {"result", "step"} <= set(changes)
        assert call(f"{url}api/events?since={seq}&topics=run&wait=0")["events"] == []
        expect(runs.locator("tbody .pill-passed")).to_have_count(1, timeout=10_000)

        browser.close()
    assert problems == []
