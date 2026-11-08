"""Failure intelligence end to end: two tests failing on one field are one cause, in History and the API."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app


def test_failures_with_one_cause_are_grouped_and_explained(workspace: Path, tmp_path: Path):
    suite = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Intel.json"
    suite.write_text(json.dumps({"testCases": [
        {"testCaseID": f"TC-INTEL-{n}", "system": "SMOKE", "module": "intel", "tags": ["intel"],
         "steps": [{"screen": "SmokeHome", "formData": {"searchBox": f"query {n}", "missing": "x"}}]}
        for n in (1, 2)]}, indent=2), encoding="utf-8")
    run = run_and_wait(workspace, tmp_path, tags=["intel"])
    assert run.status == "failed", "\n".join(run.log[-40:])
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        causes = json.loads(urllib.request.urlopen(f"{url}api/v1/runs/{run.id}/causes", timeout=20).read())
        assert causes["groups"] == 1 and causes["tests"] == 2, causes
        group = causes["items"][0]
        assert group["where"] == "SmokeHome > missing" and group["kind"] == "not-found", group
        assert "one fix on the page mends them all" in " ".join(group["explanation"])

        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="History").click()
        page.get_by_role("region", name="Runs").locator("tbody tr").first.click()
        why = page.get_by_test_id("causes")
        expect(why).to_contain_text("2 failures, 1 cause")
        expect(why).to_contain_text("2 tests fail on SmokeHome > missing: element not found in time")
        expect(why).to_contain_text("Check missing's locator on SmokeHome")
        browser.close()
    assert problems == []
