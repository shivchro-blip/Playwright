"""Application map end to end: the Map tab draws the screens, marks the untested one, and opens a page."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def test_the_map_shows_screens_moves_and_untested_screens(workspace: Path, tmp_path: Path):
    pages = workspace / "Pages" / "SMOKE"
    (pages / "Orphan.json").write_text(json.dumps({"fields": {"x": {"locator": "#x", "type": "text"}}}),
                                       encoding="utf-8")
    (pages / "Results.json").write_text(json.dumps({"fields": {"y": {"locator": "#y", "type": "text"}}}),
                                        encoding="utf-8")
    (workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Map.json").write_text(json.dumps({"testCases": [
        {"testCaseID": "TC-MAP", "system": "SMOKE", "tags": ["map"],
         "steps": [{"screen": "SmokeHome", "formData": {"searchBox": "x"}}, {"screen": "Results", "formData": {}}]}]}),
        encoding="utf-8")
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        data = json.loads(urllib.request.urlopen(url + "api/v1/map/SMOKE", timeout=20).read())
        by = {s["name"]: s for s in data["screens"]}
        assert by["Orphan"]["state"] == "untested" and "TC-MAP" in by["Results"]["tests"]
        assert {"source": "SmokeHome", "target": "Results", "tests": 1} in data["moves"]

        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Map").click()
        expect(page.get_by_test_id("map-summary")).to_contain_text("untested")
        expect(page.get_by_test_id("map-graph").locator("svg")).to_be_visible()  # Mermaid drew it
        expect(page.get_by_test_id("map-graph")).to_contain_text("Orphan")
        page.get_by_test_id("map-filter").get_by_text("Needs attention").click()
        screens = page.get_by_test_id("map-screens")
        expect(screens.locator("[data-screen=Orphan]")).to_contain_text("untested")
        expect(screens.locator("[data-screen=SmokeHome]")).to_have_count(0)
        screens.locator("[data-screen=Orphan]").get_by_role("button", name="Orphan").click()
        expect(page.get_by_role("heading", name="Orphan", level=2)).to_be_visible()  # opened in the Generator
        browser.close()
    assert problems == []
