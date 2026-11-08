"""Step time travel end to end: the page is saved after each step, and a failing run is compared step by step with
the last passing run, in Test details."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_runs import run_and_wait
from tests.e2e.test_ui import running_app
from utils.step_capture import folder_name


def test_steps_are_pictured_and_compared_with_the_last_pass(workspace: Path, tmp_path: Path):
    suite = workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "Travel.json"
    suite.write_text(json.dumps({"testCases": [{"testCaseID": "TC-TRAVEL", "system": "SMOKE", "module": "travel",
                                                "tags": ["travel"], "steps": [
                                                    {"screen": "SmokeHome", "formData": {"searchBox": "first"}},
                                                    {"screen": "SmokeHome", "formData": {"notes": "second"}}]}]}),
                     encoding="utf-8")
    options = {"headlessMode": True, "stepCapture": True}
    good = run_and_wait(workspace, tmp_path, tags=["travel"], options=options)
    assert good.status == "passed", "\n".join(good.log[-40:])
    (result,) = good.results.values()
    folder = good.results_dir / "steps" / folder_name(result.nodeid)
    assert sorted(p.name for p in folder.iterdir()) == ["1.html", "1.jpg", "1.json", "2.html", "2.jpg", "2.json"]
    assert json.loads((folder / "2.json").read_text())["url"].endswith("/index.html")

    page_file = workspace / "Pages" / "SMOKE" / "SmokeHome.json"
    page = json.loads(page_file.read_text(encoding="utf-8"))
    page["fields"]["notes"]["locator"] = "#notes-renamed"
    page_file.write_text(json.dumps(page), encoding="utf-8")
    bad = run_and_wait(workspace, tmp_path, tags=["travel"], options=options)
    assert bad.status == "failed"

    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        ui = browser.new_page(viewport={"width": 1440, "height": 1000})
        ui.on("pageerror", lambda e: problems.append(str(e)))
        ui.goto(url, wait_until="networkidle")
        ui.get_by_role("tab", name="History").click()
        ui.get_by_role("region", name="Runs").locator("tbody tr").first.click()
        ui.get_by_role("region", name="Run details").locator("tbody tr").filter(has_text="TRAVEL").click()
        travel = ui.get_by_role("dialog", name="Test details").get_by_test_id("step-travel")
        expect(travel).to_contain_text(f"compared with the last pass, run {good.id}")
        expect(travel.get_by_test_id("travel-diffs")).to_contain_text("Step 2: SmokeHome")
        expect(travel.get_by_test_id("travel-diffs")).to_contain_text("failed here")
        expect(travel.get_by_test_id("travel-step")).to_have_text("Step 2: SmokeHome")  # opens at the first difference
        expect(travel.locator("img")).to_have_count(2)  # this run and the last pass, side by side
        travel.get_by_test_id("travel-prev").click()
        expect(travel.get_by_test_id("travel-step")).to_have_text("Step 1: SmokeHome")
        shot = travel.locator("img").first.get_attribute("src")
        response = ui.request.get(url.rstrip("/") + shot)
        assert response.ok and response.headers["content-type"].startswith("image/jpeg")
        html = ui.request.get(url.rstrip("/") + shot.replace(".jpg", ".html"))
        assert "sandbox" in html.headers.get("content-security-policy", "")  # the tested site's HTML cannot run here
        browser.close()
    assert problems == []
