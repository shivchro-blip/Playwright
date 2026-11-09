"""Deploy-aware releases end to end: a deploy runs the journeys, a release check is made from that run, and GO is
reported to the pipeline's callback and as a GitHub commit status (both served here by a local stand-in)."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def receiver():
    got: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            got.append({"path": self.path, "auth": self.headers.get("Authorization", ""), "body": body})
            self.send_response(201)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", got


def call(url: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def test_a_deploy_carries_its_release_check(workspace: Path, tmp_path: Path):
    (workspace / "Data" / "journeys.json").write_text(json.dumps({"journeys": [
        {"id": "search", "name": "Search", "system": "SMOKE", "criticality": "critical",
         "tests": ["TC-SMOKE-001-Search"]}]}), encoding="utf-8")
    server, hook, got = receiver()
    problems: list[str] = []
    try:
        with running_app(tmp_path, RELEASE_GITHUB_TOKEN="ghp_test", RELEASE_GITHUB_API_URL=hook,
                         NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost") as url, \
                sync_playwright() as p:
            deploy = call(url + "api/v1/deploys", "POST", {
                "system": "SMOKE", "env": "dev", "build": "482", "repo": "acme/portal", "sha": "abc1234",
                "callback_url": hook + "/ci-hook"})
            assert deploy["status"] == "running" and deploy["run_id"]
            deadline = time.monotonic() + 240
            while deploy["status"] == "running" and time.monotonic() < deadline:
                time.sleep(1)
                deploy = call(f"{url}api/v1/deploys/{deploy['id']}")
            assert deploy["status"] == "decided" and deploy["decision"] == "go", deploy
            statuses = [g["body"]["state"] for g in got if g["path"] == "/repos/acme/portal/statuses/abc1234"]
            assert statuses == ["pending", "success"] and all(g["auth"] == "Bearer ghp_test" for g in got
                                                              if "statuses" in g["path"])
            callback = next(g["body"] for g in got if g["path"] == "/ci-hook")
            assert callback["decision"] == "go" and callback["release"] == deploy["release_id"]
            pack = call(f"{url}api/v1/releases/{deploy['release_id']}")
            assert pack["decision"] == "go" and pack["created_by"].startswith("deploy")

            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.goto(url, wait_until="networkidle")
            page.get_by_role("tab", name="Releases").click()
            row = page.get_by_test_id("deploys").locator(f"[data-deploy='{deploy['id']}']")
            expect(row).to_contain_text("GO")
            expect(row).to_contain_text("build 482")
            expect(row).to_contain_text("github success: sent")
            browser.close()
    finally:
        server.shutdown()
    assert problems == []
