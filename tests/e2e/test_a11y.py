"""Accessibility scan: the checks on a page with known problems, and a run with a11yScan on."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from tests.e2e.test_runs import run_and_wait
from utils import a11y


async def scan_page(url: str) -> list[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(url)
        findings = await a11y.scan(page)
        await browser.close()
    return findings


def test_the_scan_finds_each_known_problem_and_nothing_else(site_url):
    findings = asyncio.run(scan_page(site_url.rstrip("/") + "/a11y.html"))
    found = {(f["rule"], f["selector"]) for f in findings}
    assert found == {
        ("document-title", "html"), ("html-lang", "html"),
        ("heading-order", "h3"), ("image-alt", "#logo"),
        ("form-label", "#no-label"), ("label-placeholder-only", "#holder"),
        ("button-name", "#empty-button"), ("link-name", "#empty-link"),
        ("duplicate-id", '[id="dup"]'), ("tabindex-positive", "#tab3"),
        ("empty-heading", "#empty-heading"), ("aria-hidden-focusable", "#hidden-link"),
    }, sorted(found)
    assert [f["impact"] for f in findings] == sorted((f["impact"] for f in findings),
                                                     key=lambda i: a11y.IMPACT_ORDER[i])  # serious first
    assert all(f["help"] and f["snippet"] for f in findings)
    assert "12 problems" in a11y.summarize(findings) and a11y.summarize([]) == "no problems found"


def test_a_run_with_a11y_scan_logs_and_attaches_findings_per_screen(workspace: Path, tmp_path: Path):
    (workspace / "Pages" / "SMOKE" / "A11yPage.json").write_text(json.dumps(
        {"path": "/a11y.html", "fields": {"good": {"locator": "#good", "type": "text"}}}), encoding="utf-8")
    (workspace / "Tests" / "SMOKE" / "MasterRegressionSuite" / "A11y.json").write_text(json.dumps({"testCases": [{
        "testCaseID": "TC-A11Y-001", "description": "A page with accessibility problems.", "system": "SMOKE",
        "module": "a11y", "environment": ["dev"], "tags": ["a11y"],
        "steps": [{"screen": "A11yPage", "formData": {"good": "typed"}},
                  {"screen": "A11yPage", "formData": {"good": "again"}}]}]}), encoding="utf-8")
    run = run_and_wait(workspace, tmp_path, tags=["a11y"], options={"headlessMode": True, "a11yScan": True})
    assert run.status == "passed", "\n".join(run.log[-40:])  # the scan never fails a test
    lines = [line for line in run.log if "[a11y]" in line]
    assert any("A11yPage: 12 problems" in line for line in lines), "\n".join(lines)
    assert any("no new problems" in line for line in lines), "the second step on the page adds nothing new"
    scans = [json.loads(f.read_text(encoding="utf-8")) for f in run.results_dir.glob("*-attachment.json")]
    scans = [a for a in scans if "findings" in a]
    assert len(scans) == 1 and scans[0]["screen"] == "A11yPage" and scans[0]["url"].endswith("/a11y.html")
    assert len(scans[0]["findings"]) == 12
    plain = run_and_wait(workspace, tmp_path, tags=["a11y"], options={"headlessMode": True})
    assert not [line for line in plain.log if "[a11y]" in line]  # off unless asked for


def test_each_finding_is_reported_once_per_page(site_url):
    class FakePage:
        url = site_url + "a11y.html#section"

        async def evaluate(self, script):
            return [{"rule": "image-alt", "impact": "serious", "selector": "#logo", "snippet": "<img>", "help": "x"}]

    attached = []
    original = a11y.attach
    a11y.attach = lambda screen, url, findings: attached.append((screen, [f["rule"] for f in findings]))
    try:
        scanner = a11y.Scanner()
        first = asyncio.run(scanner.after_screen(FakePage(), "Home"))
        second = asyncio.run(scanner.after_screen(FakePage(), "Home"))
    finally:
        a11y.attach = original
    assert len(first) == 1 and second == [] and attached == [("Home", ["image-alt"])]
