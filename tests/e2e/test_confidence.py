"""The confidence hub: one verdict per environment from real journey results, over the API and on its tab."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app


def journeys(workspace: Path, broken_criticality: str) -> None:
    (workspace / "Data" / "journeys.json").write_text(json.dumps({"journeys": [
        {"id": "search", "name": "Search the site", "system": "SMOKE", "criticality": "critical", "owner": "Web",
         "tests": ["TC-SMOKE-001-Search"]},
        {"id": "broken", "name": "Broken flow", "system": "SMOKE", "criticality": broken_criticality,
         "tests": ["TC-SMOKE-004-MissingElement"]}]}), encoding="utf-8")


def test_the_hub_says_whether_each_environment_is_safe(workspace: Path, tmp_path: Path):
    journeys(workspace, "normal")
    run = run_and_wait(workspace, tmp_path, tags=["smoke", "missing"])
    assert run.status == "failed"  # TC-SMOKE-004 fails by design
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        hub = json.loads(urllib.request.urlopen(f"{url}api/v1/confidence?system=SMOKE", timeout=30).read())
        dev = next(e for e in hub["envs"] if e["env"] == "dev")
        assert dev["verdict"] == "at_risk" and dev["journeys"] == {"failing": 1, "passing": 1}
        assert dev["reasons"][0].startswith("Broken flow is failing")
        assert [j["name"] for j in hub["journeys"]] == ["Search the site", "Broken flow"]

        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(f"{url}#confidence", wait_until="networkidle")
        expect(page.get_by_role("tab", name="Confidence")).to_have_attribute("aria-selected", "true")
        card = page.locator("[data-testid=confidence-envs] [data-env=dev]")
        expect(card).to_have_attribute("data-verdict", "at_risk")
        expect(card).to_contain_text("Broken flow is failing")
        matrix = page.get_by_test_id("confidence-matrix")
        expect(matrix.locator("[data-journey=search] [data-env=dev]")).to_contain_text("passing")
        expect(matrix.locator("[data-journey=broken] [data-env=dev]")).to_contain_text("failing")

        journeys(workspace, "critical")  # the broken flow turns out to matter: the environment is blocked
        page.reload(wait_until="networkidle")
        expect(card).to_have_attribute("data-verdict", "blocked")
        expect(card).to_contain_text("critical")
        browser.close()
    assert problems == []
