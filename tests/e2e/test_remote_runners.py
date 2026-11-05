"""Remote runner end to end: a real app, a real runner agent process, the local fixture site."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import REPO_ROOT, running_app
from webapp.services.runners import PROTOCOL, RunnerStore

TIMEOUT = 240


def call(url: str, method: str = "GET", body: dict | None = None, token: str = ""):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def start_agent(url: str, token: str, tmp_path: Path, *extra: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    log = (tmp_path / "agent.log").open("w", encoding="utf-8")
    return subprocess.Popen([sys.executable, "-m", "webapp.runner_agent", "--server", url.rstrip("/"),
                             "--token", token, "--once", "--poll", "0.5", *extra], cwd=REPO_ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT)


def wait_finished(url: str, run_id: str, agent: subprocess.Popen, tmp_path: Path) -> dict:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        _, run = call(f"{url}api/runs/{run_id}")
        if run["status"] not in ("queued", "running") and run["report_status"] not in ("", "generating"):
            agent.wait(timeout=60)
            return run
        time.sleep(0.5)
    agent.kill()
    pytest.fail(f"run {run_id} did not finish:\n{(tmp_path / 'agent.log').read_text(encoding='utf-8')}")


def new_run(url: str, runner: str, **extra) -> str:
    status, run = call(f"{url}api/runs", "POST", {
        "system": "SMOKE", "env": "dev", "tags": ["smoke"], "browsers": ["chromium"], "runner": runner,
        "options": {"headlessMode": True}, **extra})
    assert status == 201, run
    return run["id"]


def test_a_run_goes_to_a_runner_and_comes_back_with_results(workspace, tmp_path):
    store = RunnerStore(tmp_path / "runners.json")
    token = store.add("lab1")
    with running_app(tmp_path) as url:
        run_id = new_run(url, "lab1")
        assert call(f"{url}api/runs/{run_id}")[1]["status"] == "queued"  # nothing runs here
        listed = call(f"{url}api/runners")[1]
        assert [(r["name"], r["online"], r["labels"], r["host"]) for r in listed] == [("lab1", False, [], "")]
        agent = start_agent(url, token, tmp_path)
        run = wait_finished(url, run_id, agent, tmp_path)
        agent_log = (tmp_path / "agent.log").read_text(encoding="utf-8")
        assert agent.returncode == 0, agent_log
        assert run["status"] == "passed", agent_log
        assert sorted(r["test_id"] for r in run["results"]) == ["TC-SMOKE-001-Search", "TC-SMOKE-002-NotesAutoGen"]
        assert run["config"]["runner"] == "lab1" and run["exit_code"] == 0
        folder = tmp_path / "runs" / run_id
        console = (folder / "console.log").read_text(encoding="utf-8")
        assert "Started on runner lab1" in (folder / "run.json").read_text() + "\n".join(
            call(f"{url}api/runs/{run_id}/log")[1]["lines"])
        assert "passed" in console.lower() or "PASSED" in console
        assert any((folder / "allure-results").iterdir()), "the runner's Allure results were uploaded"
        assert (folder / "report.html").is_file()
        _, runners = call(f"{url}api/runners")
        assert runners[0]["online"] is True


def test_the_runner_api_checks_tokens_runs_and_versions(workspace, tmp_path):
    store = RunnerStore(tmp_path / "runners.json")
    one, two = store.add("lab1"), store.add("lab2")
    with running_app(tmp_path) as url:
        base = f"{url}runner-api"
        assert call(f"{base}/claim", "POST", {"protocol": PROTOCOL})[0] == 401  # no token
        assert call(f"{base}/claim", "POST", {"protocol": PROTOCOL}, token="apr_wrong")[0] == 401
        assert call(f"{base}/claim", "POST", {"protocol": PROTOCOL}, token="not-a-runner-token")[0] == 401
        assert call(f"{base}/claim", "POST", {"protocol": PROTOCOL}, token=one)[0] == 204  # nothing queued
        status, detail = call(f"{base}/claim", "POST", {"protocol": PROTOCOL + 1}, token=one)
        assert status == 409 and "update the runner" in detail
        run_id = new_run(url, "lab1")
        status, claimed = call(f"{base}/claim", "POST", {"protocol": PROTOCOL}, token=one)
        assert status == 200 and claimed["run"]["id"] == run_id and claimed["run"]["config"]["runner"] == "lab1"
        # another runner cannot read or change lab1's run
        for method, path, body in (("GET", "workspace", None), ("POST", "events", {"lines": ["x"]}),
                                   ("POST", "finish", {"exit_code": 0})):
            assert call(f"{base}/runs/{run_id}/{path}", method, body, token=two)[0] == 404
        assert call(f"{base}/runs/{run_id}/events", "POST", {"lines": ["hello"]}, token=one) == (200, {"cancel": False})
        assert call(f"{base}/runs/no-such-run/events", "POST", {"lines": []}, token=one)[0] == 404
        assert call(f"{url}api/runs/{run_id}/cancel", "POST")[0] == 200
        assert call(f"{base}/runs/{run_id}/events", "POST", {"lines": []}, token=one) == (200, {"cancel": True})
        assert call(f"{base}/runs/{run_id}/finish", "POST", {"exit_code": -15}, token=one)[0] == 200
        assert call(f"{url}api/runs/{run_id}")[1]["status"] == "cancelled"
        # finished: the run is closed to its runner
        assert call(f"{base}/runs/{run_id}/events", "POST", {"lines": []}, token=one)[0] == 404


def test_cancelling_stops_the_agent_and_the_run(workspace, tmp_path):
    store = RunnerStore(tmp_path / "runners.json")
    token = store.add("lab1")
    with running_app(tmp_path) as url:
        run_id = new_run(url, "lab1")
        assert call(f"{url}api/runs/{run_id}/cancel", "POST")[0] == 200  # cancelled while it waits
        assert call(f"{url}api/runs/{run_id}")[1]["status"] == "cancelled"
        run_id = new_run(url, "lab1")
        agent = start_agent(url, token, tmp_path)
        deadline = time.monotonic() + 60
        while call(f"{url}api/runs/{run_id}")[1]["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.2)
        call(f"{url}api/runs/{run_id}/cancel", "POST")
        run = wait_finished(url, run_id, agent, tmp_path)
        assert run["status"] == "cancelled"


def test_the_agent_refuses_plain_http_to_a_remote_server(tmp_path):
    result = subprocess.run([sys.executable, "-m", "webapp.runner_agent", "--server", "http://autoplay.example.com",
                             "--token", "apr_x", "--once"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and "Refusing plain http" in result.stderr
    missing = subprocess.run([sys.executable, "-m", "webapp.runner_agent"], cwd=REPO_ROOT, capture_output=True,
                             text=True, timeout=60, env={k: v for k, v in os.environ.items()
                                                         if k not in ("AUTOPLAY_SERVER", "AUTOPLAY_RUNNER_TOKEN")})
    assert missing.returncode == 2 and "--server" in missing.stderr


def test_run_on_menu_sends_a_run_to_the_chosen_runner(workspace, tmp_path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        expect(page.locator("#sel-runner")).to_be_hidden()  # no runners yet: nothing to choose
        RunnerStore(tmp_path / "runners.json").add("lab1")
        select = page.locator("#sel-runner")
        expect(select).to_be_visible()  # the page notices the new runner by itself
        select.click()
        page.get_by_role("option", name="lab1 (offline)").click()
        page.get_by_test_id("run-button").click()
        expect(page.get_by_text("sent to runner lab1 (offline")).to_be_visible()
        expect(page.locator(".any-meta")).to_contain_text("on lab1")
        browser.close()
    runs = list((tmp_path / "runs").glob("*/run.json"))
    assert len(runs) == 1 and json.loads(runs[0].read_text(encoding="utf-8"))["config"]["runner"] == "lab1"
    assert json.loads(runs[0].read_text(encoding="utf-8"))["status"] == "queued"


def test_runners_dialog_adds_labels_and_removes_a_runner(workspace, tmp_path):
    with running_app(tmp_path) as url, sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="networkidle")
        page.get_by_test_id("runners").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_test_id("runners-empty")).to_be_visible()
        dialog.get_by_test_id("runner-name").fill("bad name!")
        dialog.get_by_test_id("runner-add").click()
        expect(page.get_by_text("A runner name has letters")).to_be_visible()

        dialog.get_by_test_id("runner-name").fill("lab9")
        dialog.get_by_test_id("runner-new-labels").fill("Windows, chrome")
        dialog.get_by_test_id("runner-add").click()
        token = dialog.get_by_test_id("runner-token").inner_text().strip()
        assert token.startswith("apr_")
        expect(dialog.get_by_test_id("runner-command")).to_contain_text(f"--token {token}")
        row = dialog.get_by_test_id("runner-lab9")
        expect(row).to_contain_text("windows, chrome")
        expect(row).to_contain_text("offline")
        assert token not in (tmp_path / "runners.json").read_text(encoding="utf-8")

        row.get_by_role("button", name="Labels of lab9").click()
        labels = page.get_by_role("dialog").last.get_by_test_id("runner-labels")
        labels.fill("windows edge")
        page.get_by_test_id("runner-labels-save").click()
        expect(dialog.get_by_test_id("runner-lab9")).to_contain_text("windows, edge")

        row.get_by_role("button", name="Remove lab9").click()
        page.get_by_role("button", name="Remove", exact=True).click()
        expect(dialog.get_by_test_id("runners-empty")).to_be_visible()
        browser.close()
    assert RunnerStore(tmp_path / "runners.json").names() == []
    audit_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["action"] == "runner.add" for line in audit_lines)


def test_run_on_offers_labels_and_a_labelled_run_waits_for_a_matching_runner(workspace, tmp_path):
    store = RunnerStore(tmp_path / "runners.json")
    token = store.add("win1", ["windows"])
    store.add("lin1", ["linux"])
    with running_app(tmp_path) as url:
        run_id = new_run(url, "label:windows")
        assert call(f"{url}api/runs/{run_id}")[1]["config"]["runner"] == "label:windows"
        assert call(f"{url}api/runs", "POST", {"system": "SMOKE", "env": "dev", "tags": ["smoke"],
                                               "runner": "label:mac"})[0] == 400
        # the linux runner never sees it; the windows runner takes it
        lin = store.add("lin2", ["linux"])
        assert call(f"{url}runner-api/claim", "POST", {"protocol": PROTOCOL}, token=lin)[0] == 204
        status, claimed = call(f"{url}runner-api/claim", "POST",
                               {"protocol": PROTOCOL, "host": "PC-7", "version": "abc"}, token=token)
        assert status == 200 and claimed["run"]["id"] == run_id
        _, runners = call(f"{url}api/runners")
        win = next(r for r in runners if r["name"] == "win1")
        assert (win["host"], win["version"], win["labels"]) == ("PC-7", "abc", ["windows"])
        assert call(f"{url}api/runs/{run_id}")[1]["claimed_by"] == "win1"
        call(f"{url}api/runs/{run_id}/cancel", "POST")
        call(f"{url}runner-api/runs/{run_id}/finish", "POST", {"exit_code": -15}, token=token)
