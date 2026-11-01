"""Security regression tests for the web app (no browser, no network).

Run:  python -m pytest tests/unit
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from webapp.security import install_security
from webapp.services.env_config import FIELD_KEYS, EnvConfigService, format_value, is_secret
from webapp.services.run_manager import RunConfig, RunManager
from webapp.services.test_catalog import TestCatalogService as CatalogService
from webapp.settings import Settings


# ───────────── run options (subprocess environment) ─────────────

@pytest.fixture
def manager(tmp_path: Path, monkeypatch) -> RunManager:
    m = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs", python_exe="python")
    monkeypatch.setattr(m, "_execute", lambda run: None)  # never spawn pytest
    return m


def _config(**kw) -> RunConfig:
    return RunConfig(**{"system": "GOOGLE", "env": "dev", "tags": ["smoke"], **kw})


@pytest.mark.parametrize("key", ["PYTHONPATH", "PYTHONSTARTUP", "LD_PRELOAD", "PATH",
                                 "AUTOMATENY_DOTENV_PATH", "AUTOMATENY_SYSTEMDATA_PATH",
                                 "systemConfig", "runTags", "del_allure", "MYAPP_PASSWORD"])
def test_run_rejects_options_outside_allowlist(manager: RunManager, key: str):
    with pytest.raises(ValueError):
        manager.start(_config(options={key: "x"}))
    assert manager.list() == []


def test_run_options_used_by_the_ui_are_accepted(manager: RunManager):
    # Same keys/values as ControlPanel._options() and the Generator "Run" button
    options = {"headlessMode": True, "browserConfigMaximized": False, "slowmo": 400, "pageWaitUntil": "load",
               "recording": False, "tracing": False, "snapshots": False,
               "browserConfigWidth": "1700", "browserConfigHeight": "750"}
    run = manager.start(_config(options=options))
    env = manager._environment(run)
    assert env["headlessMode"] == "true" and env["slowmo"] == "400" and env["systemConfig"] == "GOOGLE"


@pytest.mark.parametrize("system", ["..", "../..", "a/b", "a\\b", "."])
def test_run_rejects_path_like_system(manager: RunManager, system: str):
    with pytest.raises(ValueError):
        manager.start(_config(system=system))


def test_catalog_ignores_path_like_system(tmp_path: Path):
    outside = tmp_path / "MasterRegressionSuite"
    outside.mkdir()
    (outside / "x.json").write_text('{"testCases": [{"testCaseID": "LEAK", "tags": ["t"], "steps": [{}]}]}')
    (tmp_path / "ws" / "Tests").mkdir(parents=True)
    catalog = CatalogService(tmp_path / "ws").load("../..")
    assert catalog.tests == [] and catalog.errors == []


# ───────────── .env writing ─────────────

@pytest.mark.parametrize("sep", ["\r", "\n", "\r\n", "\x0b", "\x0c", "\x1c", "\x85", " ", " "])
def test_env_save_cannot_inject_keys(tmp_path: Path, sep: str):
    svc = EnvConfigService(tmp_path / ".env")
    svc.save({"runTags": f"smoke{sep}MYAPP_PASSWORD=injected{sep}PYTHONPATH=x"})
    parsed = svc._parse()
    assert set(parsed) == {"runTags"}
    assert "\n" not in parsed["runTags"]


def test_env_save_still_preserves_other_lines(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# comment\nMYAPP_PASSWORD=keep\nslowmo=0\n", encoding="utf-8")
    EnvConfigService(path).save({"slowmo": 250, "headlessMode": True})
    assert path.read_text(encoding="utf-8") == "# comment\nMYAPP_PASSWORD=keep\nslowmo=250\nheadlessMode=true\n"


def test_format_value_unchanged_for_normal_values():
    assert format_value(True) == "true"
    assert format_value(["a", " b ", ""]) == "a,b"
    assert format_value(3.0) == "3"
    assert format_value("  x y  ") == "x y"


@pytest.mark.parametrize("key", ["MYAPP_PWD", "SITE_PASS", "STRIPE_KEY", "DB_CREDENTIAL", "AUTH_COOKIE",
                                 "PRIVATE_KEY", "MYAPP_PASSWORD", "API_TOKEN"])
def test_secret_like_keys_are_hidden(tmp_path: Path, key: str):
    assert is_secret(key)
    path = tmp_path / ".env"
    path.write_text(f"{key}=s3cr3t\nreportsDir=out\n", encoding="utf-8")
    assert EnvConfigService(path).load_other() == {"reportsDir": "out"}


def test_typed_settings_are_not_treated_as_secrets():
    # env.save() refuses secret-looking keys; the Control tab must still be able to save every typed setting
    assert [k for k in FIELD_KEYS if is_secret(k)] == []


# ───────────── Host / Origin checks ─────────────

def _client(**settings) -> TestClient:
    app = FastAPI()
    install_security(app, Settings(**settings))

    @app.get("/api/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.post("/api/ping")
    def ping_post() -> dict:
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    @app.get("/runs/{rest:path}")
    def runs(rest: str) -> dict:
        return {"rest": rest}

    return TestClient(app, base_url="http://127.0.0.1:8080")


def test_loopback_host_and_same_origin_allowed():
    c = _client()
    assert c.get("/api/ping").status_code == 200
    assert c.get("/api/ping", headers={"host": "localhost:8080"}).status_code == 200
    assert c.post("/api/ping", headers={"origin": "http://127.0.0.1:8080"}).status_code == 200


def test_foreign_host_rejected():
    # DNS rebinding: the browser sends the attacker's host name
    assert _client().get("/api/ping", headers={"host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://127.0.0.1:3000"])
def test_cross_origin_requests_rejected(origin: str):
    assert _client().post("/api/ping", headers={"origin": origin}).status_code == 403


def test_configured_host_and_extra_hosts_allowed():
    c = _client(host="10.0.0.5", allowed_hosts="automateny.corp")
    assert c.get("/api/ping", headers={"host": "10.0.0.5:8080"}).status_code == 200
    assert c.get("/api/ping", headers={"host": "automateny.corp"}).status_code == 200
    assert c.get("/api/ping", headers={"host": "evil.example"}).status_code == 400


def test_wildcard_bind_without_allowed_hosts_keeps_working():
    # Existing AUTOMATENY_HOST=0.0.0.0 setups must not break
    c = _client(host="0.0.0.0")
    assert c.get("/api/ping", headers={"host": "any.name"}).status_code == 200


def test_wildcard_bind_still_refuses_cross_site_requests():
    c = _client(host="0.0.0.0")
    assert c.post("/api/ping", headers={"host": "10.0.0.5:8080", "origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/ping", headers={"host": "10.0.0.5:8080", "origin": "http://10.0.0.5:8080"}).status_code == 200


def test_proxy_host_in_allowed_hosts_is_accepted_as_origin():
    c = _client(allowed_hosts="automateny.corp")
    assert c.post("/api/ping", headers={"origin": "https://automateny.corp"}).status_code == 200


def test_websocket_scope_is_checked():
    # NiceGUI's socket.io is mounted inside the app, so the same middleware covers it
    c = _client()
    with c.websocket_connect("/ws", headers={"host": "127.0.0.1:8080", "origin": "http://127.0.0.1:8080"}) as ws:
        assert ws.receive_text() == "hi"
    for headers in ({"host": "evil.example"}, {"host": "127.0.0.1:8080", "origin": "http://evil.example"}):
        with pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)):
            with c.websocket_connect("/ws", headers=headers) as ws:
                ws.receive_text()


def test_run_artifacts_are_sandboxed():
    c = _client()
    r = c.get("/runs/x/allure-report/data/attachments/abc.html")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    r = c.get("/runs/x/allure-report/index.html")
    assert "content-security-policy" not in r.headers  # the report itself needs scripts
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in c.get("/api/ping").headers


def test_saved_browser_states_are_never_served():
    c = _client()  # a checkpoint holds session cookies: the files must not be reachable under /runs
    for path in ("/runs/20261031-1/checkpoints/TC-1/2.json", "/runs/x/checkpoints/", "/runs/x/a/checkpoints/b.json"):
        assert c.get(path).status_code == 404
