"""Control plane: create tenants that run as their own AutoPlay, read their health, start and stop them."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.e2e.conftest import free_port
from tests.e2e.test_ui import REPO_ROOT


def call(url: str, method: str = "GET", body: dict | None = None, token: str = ""):
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def wait_for(check, what: str, seconds: float = 90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def test_tenants_run_isolated_and_the_portfolio_reads_them(workspace: Path, tmp_path: Path):
    cp_dir = tmp_path / "cp"
    first = free_port()
    # something else holds the port after the first tenant's (on Windows an outgoing connection often does):
    # the next tenant must get a port that is really free
    held = socket.socket()
    try:
        held.bind(("127.0.0.1", first + 1))
    except OSError:
        pass  # already taken by something else: just as good
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "AUTOPLAY_"))}
    env.update({"AUTOPLAY_CP_DIR": str(cp_dir), "AUTOPLAY_CP_FIRST_PORT": str(first), "PYTHONUNBUFFERED": "1"})
    created = subprocess.run([sys.executable, "-m", "webapp.control", "tenant", "create", "Licensing team",
                              "--admin", "lead", "--owner", "QA", "--template", str(workspace)],
                             cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert created.returncode == 0, created.stderr
    assert "password (shown once)" in created.stdout
    tenant_ws = cp_dir / "tenants" / "licensing-team"
    assert (tenant_ws / "Pages" / "SMOKE").is_dir() and (tenant_ws / ".env").read_text() == ""
    tenants = json.loads((cp_dir / "tenants.json").read_text())["tenants"]
    assert tenants[0]["managed"] and "TENANT_LICENSING_TEAM_TOKEN=" in (cp_dir / "secrets.env").read_text()

    port = free_port()
    log = (tmp_path / "cp.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "webapp.control", "serve"], cwd=REPO_ROOT,
                            env={**env, "AUTOPLAY_CP_PORT": str(port)}, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_for(lambda: call(f"{url}/cp/api/tenants")[0] == 200, "the control plane")
        tenant_url = tenants[0]["url"]
        # the supervisor starts the tenant: its own AutoPlay, with sign-in required
        wait_for(lambda: call(f"{tenant_url}/api/summary")[0] == 401, "the tenant to start")

        def green():
            status, body = call(f"{url}/cp/api/portfolio?fresh=true")
            return body if status == 200 and body["tenants"][0]["reachable"] else None
        portfolio = wait_for(green, "the tenant's summary")
        first = portfolio["tenants"][0]
        assert first["health"] == "green" and first["summary"]["systems"] == ["SMOKE"]
        assert first["process"]["process"] == "running"

        # a second, blank tenant from the API; each one's token reads only that one
        status, made = call(f"{url}/cp/api/tenants", "POST", {"name": "Payments", "admin": "boss"})
        assert status == 201 and made["admin_password"] and made["tenant"]["managed"]
        second_url = made["tenant"]["url"]
        wait_for(lambda: call(f"{second_url}/api/summary")[0] == 401, "the second tenant")
        tokens = dict(line.split("=", 1) for line in (cp_dir / "secrets.env").read_text().split())
        a_token, b_token = tokens["TENANT_LICENSING_TEAM_TOKEN"], tokens["TENANT_PAYMENTS_TOKEN"]
        assert call(f"{second_url}/api/summary", token=b_token)[1]["systems"] == []
        assert call(f"{second_url}/api/summary", token=a_token)[0] == 401  # one tenant's key opens only it
        assert call(f"{tenant_url}/api/journeys?system=SMOKE", token=b_token)[0] == 401
        status, _ = call(f"{tenant_url}/api/runs", "POST", {"system": "SMOKE"}, token=a_token)
        assert status in (403, 422)  # the service account only reads
        assert call(f"{url}/cp/api/tenants", "POST", {"name": "Bad", "url": "ftp://x", "token": "x" * 10})[0] == 400

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url, wait_until="networkidle")
            expect(page.get_by_test_id("cp-row-licensing-team")).to_contain_text("Licensing team")
            expect(page.get_by_test_id("cp-health-licensing-team")).to_contain_text("Healthy")
            expect(page.get_by_test_id("cp-process-licensing-team")).to_have_text("running")
            expect(page.get_by_test_id("cp-row-payments")).to_be_visible()

            # register an instance that is not there: shown as unknown with the reason
            page.get_by_test_id("cp-new").click()
            page.get_by_test_id("cp-mode").get_by_text("Register one that runs elsewhere").click()
            page.get_by_test_id("cp-name").fill("Old team")
            page.get_by_test_id("cp-url").fill(f"http://127.0.0.1:{free_port()}")
            page.get_by_test_id("cp-token").fill("any_notarealtoken")
            page.get_by_test_id("cp-save").click()
            expect(page.get_by_text("Registered Old team")).to_be_visible()
            expect(page.get_by_test_id("cp-health-old-team")).to_contain_text("Unknown")
            expect(page.get_by_test_id("cp-attention")).to_contain_text("Not reachable")

            # create one from the page: the password is shown once
            page.get_by_test_id("cp-new").click()
            page.get_by_test_id("cp-name").fill("Renewals")
            page.get_by_test_id("cp-admin").fill("owner1")
            page.get_by_test_id("cp-save").click()
            expect(page.get_by_test_id("cp-created")).to_contain_text("Renewals is starting")
            assert len(page.get_by_test_id("cp-password").inner_text()) >= 10
            page.get_by_role("button", name="Done").click()

            page.get_by_test_id("cp-stop-payments").click()
            expect(page.get_by_text("Stopped payments")).to_be_visible()
            expect(page.get_by_test_id("cp-process-payments")).to_have_text("stopped")
            browser.close()
        wait_for(lambda: call(f"{second_url}/api/summary")[0] == 0, "the stopped tenant to go away", 30)
        assert call(f"{url}/cp/api/tenants/payments/restart", "POST")[0] == 200
        wait_for(lambda: call(f"{second_url}/api/summary")[0] == 401, "the restarted tenant")
        assert call(f"{url}/cp/api/tenants/old-team", "DELETE")[0] == 200
        assert [t["id"] for t in call(f"{url}/cp/api/tenants")[1]] == ["licensing-team", "payments", "renewals"]
    finally:
        proc.kill()  # the hardest way to end it: no shutdown handlers run
        proc.wait(20)
        log.close()
        held.close()
    # its tenants notice and stop too (their stdin, a pipe from the control plane, closed)
    wait_for(lambda: call(f"{tenant_url}/api/summary")[0] == 0, "the tenants to stop with it", 30)
    wait_for(lambda: call(f"{second_url}/api/summary")[0] == 0, "the second tenant to stop with it", 30)
    assert (tenant_ws / "reports" / "users.json").is_file()  # each tenant's users live in its own workspace
