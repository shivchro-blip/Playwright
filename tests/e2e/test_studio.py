"""The Studio end to end: open the live page, pick elements on its picture, run each step, save, and the saved test
case passes a real run."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app
from webapp.services.studio import VIEWPORT


def api(url: str, path: str, body: dict | None = None):
    req = urllib.request.Request(url + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def test_author_a_test_on_the_live_page(workspace: Path, tmp_path: Path, site_url: str):
    problems: list[str] = []
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        # where the smoke page's boxes are at the Studio's viewport (the Studio's browser renders the same page)
        probe = browser.new_page(viewport=VIEWPORT)
        probe.goto(f"{site_url}/index.html")
        spots = {}
        for sel in ("#q", "#notes"):
            b = probe.locator(sel).bounding_box()
            spots[sel] = (b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        probe.close()

        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(url, wait_until="networkidle")
        page.get_by_role("tab", name="Generator").click()
        page.get_by_test_id("tab-studio").click()
        expect(page.get_by_role("tab", name="Studio")).to_have_attribute("aria-selected", "true")
        page.get_by_test_id("studio-url").fill(f"{site_url}/index.html")
        page.get_by_test_id("studio-open").click()
        picture = page.get_by_test_id("studio-picture").locator("img")
        expect(picture).to_have_attribute("src", re.compile("^data:image/jpeg"), timeout=30_000)

        def click_spot(sel: str) -> None:
            box = picture.bounding_box()
            scale = box["width"] / VIEWPORT["width"]
            x, y = spots[sel]
            picture.click(position={"x": x * scale, "y": y * scale})

        click_spot("#q")
        picked = page.get_by_test_id("studio-picked")
        expect(picked).to_contain_text("#q")
        page.get_by_test_id("studio-value").fill("studio search")
        page.get_by_test_id("studio-run").click()
        steps = page.get_by_test_id("studio-steps")
        expect(steps.locator("li")).to_have_count(1)
        expect(steps).to_contain_text("studio search")

        click_spot("#notes")
        expect(picked).to_contain_text("#notes")
        page.get_by_test_id("studio-value").fill("made in the studio")
        page.get_by_test_id("studio-run").click()
        expect(steps.locator("li")).to_have_count(2)

        page.get_by_test_id("studio-page").fill("StudioPage")
        page.keyboard.press("Enter")
        page.get_by_test_id("studio-tc").fill("TC-SMOKE-STUDIO-1")
        page.get_by_test_id("studio-tags").fill("studio")
        page.get_by_test_id("studio-save").click()
        expect(page.locator("#tc-id")).to_have_value("TC-SMOKE-STUDIO-1", timeout=15_000)
        browser.close()

    saved_page = json.loads((workspace / "Pages" / "SMOKE" / "StudioPage.json").read_text(encoding="utf-8"))
    fields = saved_page["fields"]
    assert {k: v["locator"] for k, v in fields.items()} == {next(iter(fields)): "#q", list(fields)[1]: "#notes"}
    assert all(f["identity"]["fingerprint"] for f in fields.values())
    tests = [json.loads(p.read_text(encoding="utf-8")) for p in (workspace / "Tests").rglob("*.json")]
    case = next(tc for data in tests for tc in data.get("testCases", []) if tc["testCaseID"] == "TC-SMOKE-STUDIO-1")
    assert case["steps"][0]["screen"] == "StudioPage"
    assert list(case["steps"][0]["formData"].values()) == ["studio search", "made in the studio"]
    assert problems == []

    # the saved test case passes a real run
    with running_app(tmp_path) as url:
        run = api(url, "api/v1/runs", {"system": "SMOKE", "env": "dev", "tags": ["studio"],
                                       "browsers": ["chromium"], "options": {"headlessMode": True}})
        deadline = time.monotonic() + 240
        while (status := api(url, f"api/v1/runs/{run['id']}")["status"]) in ("queued", "running"):
            assert time.monotonic() < deadline
            time.sleep(1)
        assert status == "passed"
