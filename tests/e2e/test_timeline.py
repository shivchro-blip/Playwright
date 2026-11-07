"""A test case's timeline: its results with the failing step, quarantine joining the story, over the UI and API."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def api(url: str, path: str, body: dict | None = None):
    req = urllib.request.Request(url + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run_tests(url: str, test_ids: list[str]) -> None:
    run = api(url, "api/v1/runs", {"system": "SMOKE", "env": "dev", "tags": ["smoke", "missing"],
                                   "test_ids": test_ids, "browsers": ["chromium"], "options": {"headlessMode": True}})
    deadline = time.monotonic() + 240
    while api(url, f"api/v1/runs/{run['id']}")["status"] in ("queued", "running"):
        assert time.monotonic() < deadline, "run did not finish"
        time.sleep(1)


def test_the_timeline_tells_a_tests_story(workspace: Path, tmp_path: Path):
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        run_tests(url, ["TC-SMOKE-004-MissingElement"])
        run_tests(url, ["TC-SMOKE-004-MissingElement", "TC-SMOKE-001-Search"])
        tl = api(url, "api/v1/tests/TC-SMOKE-004-MissingElement/timeline?system=SMOKE")
        assert tl["results"] == 2 and tl["pass_rate"] == 0 and tl["state"] == "failing"
        assert [e["kind"] for e in tl["entries"]] == ["result", "result"]
        assert tl["entries"][0]["detail"].startswith("step 1 of 1: SmokeHome")

        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.keyboard.press("Control+k")
        box = page.get_by_test_id("palette-input")
        expect(box).to_have_value("")
        box.fill("TC-SMOKE-004")
        box.press("Enter")
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-004-MissingElement")
        expect(page.get_by_role("tab", name="Test cases")).to_have_attribute("aria-selected", "true")
        page.get_by_test_id("tc-timeline").click()
        timeline = page.get_by_test_id("timeline")
        expect(timeline.get_by_test_id("timeline-state")).to_have_text("Failing")
        expect(timeline.get_by_test_id("timeline-summary")).to_contain_text("0%")
        entries = timeline.get_by_test_id("timeline-entries").locator("li")
        expect(entries).to_have_count(2)
        expect(entries.first).to_contain_text("step 1 of 1: SmokeHome")

        timeline.get_by_test_id("timeline-quarantine").click()
        page.get_by_test_id("quarantine-reason").fill("element removed in the redesign")
        page.get_by_test_id("quarantine-ok").click()
        expect(timeline).to_contain_text("Quarantined since")
        expect(entries.first).to_contain_text("element removed in the redesign")
        expect(timeline.get_by_test_id("timeline-quarantine")).to_have_text("Release")
        browser.close()
    assert problems == []
