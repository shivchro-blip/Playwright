"""Control plane: tenants and their tokens, isolated workspaces, health, the portfolio and the supervisor."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from webapp.control.portfolio import Portfolio, fetch, rollup
from webapp.control.supervisor import ProcessState, Supervisor, child_env
from webapp.control.tenants import (
    SERVICE_USER, Tenant, TenantError, TenantStore, check_url, create_accounts, create_workspace,
)
from webapp.services.summary import health
from webapp.services.users import UserStore


def summary(**over) -> dict:
    base = {"app": "autoplay", "journeys": {"total": {"passing": 3}, "attention": [], "by_system": {}},
            "releases": [], "monitors": [], "runs": {"active": 1, "queued": 0, "last_24h": {"passed": 4}},
            "runners": {"total": 2, "online": 1}}
    base.update(over)
    return base


def test_store_tokens_and_validation(tmp_path):
    store = TenantStore(tmp_path)
    store.add(Tenant("team-a", "Team A", "http://127.0.0.1:8101/", managed=True, port=8101))
    with pytest.raises(TenantError, match="already"):
        store.add(Tenant("team-a", "Again", "http://127.0.0.1:9999"))
    with pytest.raises(TenantError, match="already uses"):
        store.add(Tenant("team-b", "Team B", "http://127.0.0.1:8101"))
    for bad in ("ftp://x", "http://", "http://x/path", "x"):
        with pytest.raises(TenantError):
            check_url(bad)
    t = store.get("team-a")
    assert t.url == "http://127.0.0.1:8101"
    with socket.socket() as busy:  # a port something on the machine holds is skipped too
        busy.bind(("127.0.0.1", 0))
        held = busy.getsockname()[1]
        store.add(Tenant("held", "Held", f"http://127.0.0.1:{held - 1}", port=held - 1))
        assert store.next_port(held - 1) not in (held - 1, held)
    store.remove("held")
    store.set_token(t, "any_abcdefgh123")
    assert store.token(t) == "any_abcdefgh123"
    if os.name != "nt":
        assert (tmp_path / "secrets.env").stat().st_mode & 0o077 == 0  # only its owner can read it
    with pytest.raises(TenantError):
        store.set_token(t, "bad token\nINJECTED=1")
    assert store.update("team-a", running=False, url="http://evil").running is False
    assert store.get("team-a").url == "http://127.0.0.1:8101"  # only name, owner, description, running change
    assert store.remove("team-a").id == "team-a" and store.list() == [] and store.token(t) == ""


def test_a_new_tenant_never_inherits_runs_users_or_secrets(tmp_path):
    template = tmp_path / "template"
    for rel, content in {"Data/systemData.json": "{}", "Pages/S/P.json": "{}", ".env": "PASSWORD=secret",
                         "reports/users.json": "{}", "reports/runs/r1/run.json": "{}",
                         "Data/locatorProfiles/S.json": "{}", "Data/monitors.json": "{}",
                         "Data/journeys.json": "{}"}.items():
        (template / rel).parent.mkdir(parents=True, exist_ok=True)
        (template / rel).write_text(content, encoding="utf-8")
    copy = tmp_path / "copy"
    create_workspace(copy, template)
    assert (copy / "Pages/S/P.json").is_file() and (copy / "Data/journeys.json").is_file()
    assert (copy / ".env").read_text() == "" and not (copy / "reports").exists()
    assert not (copy / "Data/locatorProfiles").exists() and not (copy / "Data/monitors.json").exists()
    with pytest.raises(TenantError, match="not empty"):
        create_workspace(copy)
    blank = tmp_path / "blank"
    create_workspace(blank)
    assert json.loads((blank / "Tests/com/commonActions.json").read_text()) == {"commonActions": {}}
    assert (blank / ".env").is_file()
    password, token = create_accounts(blank, "lead")
    users = UserStore(blank / "reports" / "users.json")
    assert users.authenticate("lead", password).role == "admin"
    assert users.by_token(token).username == SERVICE_USER and users.get(SERVICE_USER).role == "viewer"
    with pytest.raises(TenantError):
        create_accounts(blank, "lead")  # already there


def test_health_levels():
    assert health(None) == ("unknown", ["not reachable"])
    assert health(summary()) == ("green", [])
    red = summary(journeys={"total": {"failing": 3}, "by_system": {},
                            "attention": [{"journey": "Pay", "system": "S", "env": "prod", "criticality": "critical"}]},
                  monitors=[{"name": "Prod", "down": 1, "down_journeys": ["Login"]}])
    state, reasons = health(red)
    assert state == "red" and reasons == ["Pay failing on S/prod (critical)", "monitor Prod: Login down",
                                          "2 other journey statuses failing"]
    assert health(summary(releases=[{"name": "R1", "decision": "NO-GO"}])) == \
        ("amber", ["latest release R1 is NO-GO"])


class Fake(BaseHTTPRequestHandler):
    answers: dict = {}

    def do_GET(self):  # noqa: N802
        token = self.headers.get("Authorization", "")
        status, body = self.answers.get(token, (401, {"detail": "no"}))
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_server():
    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_fetch_and_rollup(tmp_path, fake_server):
    Fake.answers = {"Bearer good": (200, summary()), "Bearer junk": (200, b"<html>"),
                    "Bearer other": (200, {"app": "something else"})}
    t = Tenant("a", "A", fake_server)
    ok = fetch(t, "good")
    assert ok.reachable and ok.health == "green" and ok.summary["runs"]["active"] == 1
    assert "refused" in fetch(t, "wrong").error
    assert "not a summary" in fetch(t, "junk").error and "not a summary" in fetch(t, "other").error
    assert "No service token" in fetch(t, "").error
    assert "Not reachable" in fetch(Tenant("b", "B", "http://127.0.0.1:9"), "x", timeout=2).error

    store = TenantStore(tmp_path)
    store.add(Tenant("a", "A", fake_server))
    store.add(Tenant("down", "Down", "http://127.0.0.1:9"))
    store.set_token(store.get("a"), "goodtoken1")
    Fake.answers["Bearer goodtoken1"] = (200, summary(monitors=[{"name": "M", "up": 1, "down": 1,
                                                                 "down_journeys": ["X"]}]))
    portfolio = Portfolio(store)
    views = portfolio.collect()
    assert [(v.tenant.id, v.health) for v in views] == [("a", "red"), ("down", "unknown")]
    assert portfolio.collect() is views  # cached for a few seconds
    total = rollup(views)
    assert total["health"] == {"red": 1, "unknown": 1} and total["monitors_down"] == 1
    assert total["runs_active"] == 1 and total["runners_online"] == 1
    assert [a["level"] for a in total["attention"]] == ["red", "unknown"]


def test_child_env_isolates_tenants(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPLAY_RUNS_DIR", "/shared/runs")
    monkeypatch.setenv("AUTOPLAY_CP_PORT", "8090")
    monkeypatch.setenv("TENANT_OTHER_TOKEN", "secret")
    t = Tenant("a", "A", "http://127.0.0.1:8101", managed=True, workspace=str(tmp_path / "ws"), port=8101)
    env = child_env(t)
    assert "AUTOPLAY_RUNS_DIR" not in env and "AUTOPLAY_CP_PORT" not in env and "TENANT_OTHER_TOKEN" not in env
    assert env["AUTOPLAY_WORKSPACE"] == str(tmp_path / "ws") and env["AUTOPLAY_PORT"] == "8101"
    assert env["AUTOPLAY_DOTENV_PATH"] == str(tmp_path / "ws" / ".env") and env["AUTOPLAY_AUTH_ENABLED"] == "true"
    assert env["AUTOPLAY_EXIT_WITH_PARENT"] == "true"


def test_supervisor_starts_restarts_and_stops(tmp_path):
    store = TenantStore(tmp_path)
    store.add(Tenant("a", "A", "http://127.0.0.1:8101", managed=True, workspace=str(tmp_path), port=8101))
    store.add(Tenant("ext", "External", "http://127.0.0.1:8200"))  # not managed: never started
    sup = Supervisor(store, sys.executable, tmp_path / "logs")
    started = []

    def fake_start(tenant):  # a short-lived process instead of the web app
        import subprocess
        s = sup.states.setdefault(tenant.id, ProcessState())
        s.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
        s.started = time.time()
        started.append(tenant.id)

    sup.start = fake_start
    sup.check()
    assert started == ["a"] and sup.state("a")["process"] == "running"
    sup.states["a"].process.wait()
    sup.check()  # it exited by itself: back-off before the restart
    assert sup.state("a")["restarts"] == 1 and started == ["a"]
    sup.states["a"].next_try = 0
    sup.check()
    assert started == ["a", "a"]
    store.update("a", running=False)
    sup.check()
    assert sup.state("a")["process"] == "stopped" and "ext" not in sup.states
