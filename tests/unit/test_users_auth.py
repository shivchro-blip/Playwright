"""Accounts, roles, API tokens, sign-in, the audit trail and the run queue."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp.services.audit import AuditLog
from webapp.services.run_manager import RunConfig, RunManager
from webapp.services.users import ROLES, UserStore, allowed, check_password, hash_password

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures" / "workspace"
PASSWORD = "correct horse battery"


def test_roles_build_on_each_other():
    assert [r for r in ROLES if allowed(r, "view")] == list(ROLES)
    assert [r for r in ROLES if allowed(r, "run")] == ["runner", "editor", "admin"]
    assert [r for r in ROLES if allowed(r, "edit")] == ["editor", "admin"]
    assert [r for r in ROLES if allowed(r, "settings")] == ["admin"]
    assert not allowed("nobody", "view")


def test_passwords_and_tokens_are_stored_hashed(tmp_path):
    store = UserStore(tmp_path / "users.json")
    store.add("ann", PASSWORD, "admin")
    store.add("bob", PASSWORD, "viewer")
    raw = store.path.read_text(encoding="utf-8")
    assert PASSWORD not in raw and raw.count("scrypt$") == 2
    assert check_password(PASSWORD, hash_password(PASSWORD)) and not check_password("nope", hash_password(PASSWORD))
    assert store.authenticate("ann", PASSWORD).role == "admin"
    assert store.authenticate("ann", "wrong") is None and store.authenticate("ghost", PASSWORD) is None
    token = store.new_token("bob", "ci")
    assert token.startswith("any_") and token not in store.path.read_text(encoding="utf-8")
    assert store.by_token(token).username == "bob" and store.by_token("any_forged") is None
    token_id = store.get("bob").tokens[0]["id"]
    assert store.revoke_token("bob", token_id) and store.by_token(token) is None
    store.update("bob", disabled=True)
    assert store.authenticate("bob", PASSWORD) is None


def test_store_validation_and_last_admin(tmp_path):
    store = UserStore(tmp_path / "users.json")
    for name, pw, role, msg in [("a b", PASSWORD, "viewer", "User name"), ("ann", "short", "viewer", "10"),
                                ("ann", PASSWORD, "root", "Role")]:
        with pytest.raises(ValueError, match=msg):
            store.add(name, pw, role)
    store.add("ann", PASSWORD, "admin")
    with pytest.raises(ValueError, match="already exists"):
        store.add("ann", PASSWORD, "admin")
    with pytest.raises(ValueError, match="active admin"):
        store.update("ann", role="viewer")
    with pytest.raises(ValueError, match="active admin"):
        store.update("ann", disabled=True)
    store.add("cy", PASSWORD, "editor")
    with pytest.raises(ValueError, match="active admin"):
        store.remove("ann")


def test_audit_log_newest_first_and_filtered(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("ann", "run.start", "r1", tags=["smoke"], empty="")
    log.record("bob", "denied", "/api/runs", permission="run")
    log.record("ann", "page.save", "S/Home.json")
    assert [e["action"] for e in log.recent()] == ["page.save", "denied", "run.start"]
    assert [e["target"] for e in log.recent(user="ann")] == ["S/Home.json", "r1"]
    assert "empty" not in log.recent(action="run.start")[0]


def test_runs_queue_instead_of_being_refused(tmp_path, monkeypatch):
    manager = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=tmp_path / "runs", python_exe="python")
    started = []
    monkeypatch.setattr(manager, "_execute", lambda run: started.append(run.id))  # stays "running"
    config = lambda: RunConfig(system="SYS", env="dev", tags=["smoke"])  # noqa: E731
    first, second, third = manager.start(config()), manager.start(config()), manager.start(config())
    assert started == [first.id] and first.status == "running"
    assert second.status == third.status == "queued"
    assert manager.queue_position(second.id) == 1 and manager.queue_position(third.id) == 2
    assert json.loads((second.dir / "run.json").read_text())["status"] == "queued"
    assert manager.cancel(second.id) and second.status == "cancelled" and manager.queue_position(third.id) == 1
    first.status = "passed"
    manager._dispatch()  # what the end of a run does
    assert started == [first.id, third.id] and manager.queue_position(third.id) is None


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """The real API router behind sign-in, on a copy of the SMOKE workspace."""
    import webapp.settings as settings_module
    import webapp.state as state_module
    ws = tmp_path / "workspace"
    shutil.copytree(FIXTURES, ws)
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    for key, value in {"AUTOMATENY_WORKSPACE": ws, "AUTOMATENY_DOTENV_PATH": tmp_path / "empty.env",
                       "AUTOMATENY_RUNS_DIR": tmp_path / "runs", "AUTOMATENY_AUTH_ENABLED": "true"}.items():
        monkeypatch.setenv(key, str(value))
    settings_module.get_settings.cache_clear()
    state_module.services.cache_clear()
    svc = state_module.services()
    svc.users.add("ann", PASSWORD, "admin")
    svc.users.add("vic", PASSWORD, "viewer")
    monkeypatch.setattr(svc.runs, "_execute", lambda run: None)
    from webapp.api import router
    from webapp.auth import install_auth
    app = FastAPI()
    install_auth(app, svc.settings)
    app.include_router(router)
    yield TestClient(app), svc
    settings_module.get_settings.cache_clear()
    state_module.services.cache_clear()


def test_api_needs_sign_in_or_a_token_and_checks_roles(app_client):
    client, svc = app_client
    assert client.get("/api/systems").status_code == 401
    assert client.get("/api/runs", headers={"Authorization": "Bearer any_nope"}).status_code == 401
    viewer = {"Authorization": f"Bearer {svc.users.new_token('vic', 'ci')}"}
    assert client.get("/api/systems", headers=viewer).status_code == 200
    body = {"system": "SMOKE", "env": "dev", "tags": ["smoke"], "browsers": ["chromium"]}
    denied = client.post("/api/runs", json=body, headers=viewer)
    assert denied.status_code == 403 and "viewer" in denied.json()["detail"]
    admin = {"Authorization": f"Bearer {svc.users.new_token('ann')}"}
    assert client.post("/api/runs", json=body, headers=admin).status_code == 201
    actions = [(e["user"], e["action"], e.get("status")) for e in svc.audit.recent()]
    assert ("ann", "api.post", 201) in actions and ("vic", "denied", None) in actions


def test_browser_sign_in_with_session_cookie_and_throttling(app_client):
    client, svc = app_client
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/"
    assert "Sign in" in client.get("/login").text
    bad = client.post("/login", data={"username": "ann", "password": "wrong", "next": "/"})
    assert "wrong" in bad.text
    ok = client.post("/login", data={"username": "ann", "password": PASSWORD, "next": "//evil.test/"},
                     follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/"  # no open redirect
    assert "automateny_session" in ok.cookies and "httponly" in ok.headers["set-cookie"].lower()
    assert client.get("/api/systems").status_code == 200  # the cookie works for the API too
    client.get("/logout")
    assert client.get("/api/systems").status_code == 401
    for _ in range(5):
        client.post("/login", data={"username": "vic", "password": "nope"})
    assert "Too many attempts" in client.post("/login", data={"username": "vic", "password": PASSWORD}).text
    assert {"login", "login.failed", "logout", "login.throttled"} <= {e["action"] for e in svc.audit.recent()}
