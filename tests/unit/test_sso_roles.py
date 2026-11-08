"""Roles per system and environment, and single sign-on with OpenID Connect (against a fake provider)."""
from __future__ import annotations

import base64
import json
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp.services.oidc import OIDCClient, RoleMap, SSOError
from webapp.services.users import User, UserStore, check_grant

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures" / "workspace"
PASSWORD = "correct horse battery"
ISSUER = "http://127.0.0.1:9/idp"


# ───── roles per system and environment ─────

def test_the_most_specific_grant_wins():
    user = User("bob", "editor", grants=[{"system": "*", "env": "prod", "role": "viewer"},
                                         {"system": "PORTAL", "env": "*", "role": "admin"},
                                         {"system": "PORTAL", "env": "prod", "role": "runner"}])
    assert user.role_for() == "editor" and user.role_for("OTHER", "dev") == "editor"
    assert user.role_for("OTHER", "prod") == "viewer" and user.role_for("OTHER", "PROD") == "viewer"
    assert user.role_for("PORTAL", "dev") == "admin" and user.role_for("PORTAL") == "admin"
    assert user.role_for("PORTAL", "prod") == "runner"
    assert user.can("edit", "OTHER", "dev") and not user.can("run", "OTHER", "prod")
    assert user.can("run", "PORTAL", "prod") and not user.can("edit", "PORTAL", "prod")
    assert not User("x", "editor", disabled=True).can("view")


def test_a_grant_can_raise_a_role_and_lets_the_api_in_somewhere():
    viewer = User("vic", "viewer", grants=[{"system": "SMOKE", "env": "dev", "role": "runner"}])
    assert not viewer.can("run") and viewer.can("run", "SMOKE", "dev") and not viewer.can("run", "SMOKE", "uat")
    assert viewer.may_somewhere("run") and not viewer.may_somewhere("edit")


@pytest.mark.parametrize("grant, message", [
    ({"role": "editor"}, "names a system, an environment or both"),
    ({"system": "A", "role": "root"}, "Role must be"),
    ({"system": "a b", "role": "viewer"}, "letters, digits"),
])
def test_grants_are_checked(grant, message):
    with pytest.raises(ValueError, match=message):
        check_grant(grant)


def test_store_grants_replace_the_same_place_and_old_files_still_load(tmp_path):
    store = UserStore(tmp_path / "users.json")
    store.path.write_text(json.dumps({"users": [{"username": "ann", "role": "admin", "password_hash": "",
                                                 "tokens": [], "disabled": False}]}), encoding="utf-8")
    assert store.get("ann").grants == [] and not store.get("ann").sso  # a file from before grants
    store.grant("ann", "viewer", env="prod")
    store.grant("ann", "runner", env="prod")
    store.grant("ann", "editor", system="PORTAL")
    assert store.get("ann").grants == [{"system": "*", "env": "prod", "role": "runner"},
                                       {"system": "PORTAL", "env": "*", "role": "editor"}]
    assert store.ungrant("ann", env="prod") and not store.ungrant("ann", env="prod")
    with pytest.raises(ValueError, match="No user"):
        store.grant("ghost", "viewer", env="prod")


def test_sso_users_are_created_updated_or_refused(tmp_path):
    store = UserStore(tmp_path / "users.json")
    with pytest.raises(PermissionError, match="no AutoPlay role"):
        store.sso_user("new@x.test", None, None)  # no mapped group, no default role
    user = store.sso_user("new@x.test", None, None, default_role="viewer")
    assert user.role == "viewer" and user.sso and user.password_hash == ""
    assert store.authenticate("new@x.test", "") is None  # no password sign-in for them
    user = store.sso_user("new@x.test", "editor", [{"system": "PORTAL", "env": "prod", "role": "viewer"}])
    assert user.role == "editor" and user.grants == [{"system": "PORTAL", "env": "prod", "role": "viewer"}]
    user = store.sso_user("new@x.test", None, None)  # in no mapped group now: keeps what it has here
    assert user.role == "editor" and user.grants
    store.add_sso("pre@x.test", "runner")
    assert store.sso_user("pre@x.test", None, None).role == "runner"  # added by an admin ahead of time
    store.add("ann", PASSWORD, "admin")
    store.update("pre@x.test", disabled=True)
    with pytest.raises(PermissionError, match="disabled"):
        store.sso_user("pre@x.test", "admin", None)
    with pytest.raises(ValueError, match="not usable"):
        store.sso_user("has space", "viewer", None)


# ───── the role map ─────

def test_role_map_gives_the_highest_role_and_the_grants_of_the_groups():
    m = RoleMap.parse("qa=editor, leads=admin; portal=PORTAL/*:editor, ops=*/prod:runner, ops2=*/prod:viewer")
    assert m.resolve(["nobody"]) == (None, None)
    assert m.resolve(["qa", "leads"]) == ("admin", [])
    role, grants = m.resolve(["portal", "ops", "ops2"])
    assert role is None and grants == [{"system": "PORTAL", "env": "*", "role": "editor"},
                                       {"system": "*", "env": "prod", "role": "runner"}]  # the higher one wins
    assert RoleMap.parse("").empty


@pytest.mark.parametrize("text", ["qa", "qa=root", "qa=PORTAL/dev:root", "=editor"])
def test_role_map_mistakes_are_reported(text):
    with pytest.raises(ValueError, match="Role map entry"):
        RoleMap.parse(text)


# ───── the OIDC client against a fake provider ─────

def jwt(claims: dict) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


class FakeIdP:
    """Discovery, token and userinfo endpoints; the ID token carries the nonce of the last authorization."""

    def __init__(self, **claims):
        self.claims = {"iss": ISSUER, "aud": "autoplay", "sub": "u1", "email": "ann@x.test",
                       "email_verified": True, "groups": ["qa"], **claims}
        self.nonce = ""
        self.token_requests: list[dict] = []
        self.userinfo = None

    def __call__(self, method, url, data, headers):
        if url.endswith("/.well-known/openid-configuration"):
            return 200, {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                         "token_endpoint": ISSUER + "/token", "userinfo_endpoint": ISSUER + "/userinfo"}
        if url.endswith("/token"):
            self.token_requests.append(data)
            if data.get("code") != "good-code":
                return 400, {"error": "invalid_grant"}
            claims = {"nonce": self.nonce, "exp": time.time() + 300, **self.claims}
            return 200, {"id_token": jwt(claims), "access_token": "at"}
        if url.endswith("/userinfo"):
            return 200, self.userinfo or {}
        return 404, {}

    def authorize(self, url: str) -> dict:
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.nonce = query["nonce"]
        return query


def test_start_sends_pkce_state_and_nonce():
    idp = FakeIdP()
    client = OIDCClient(ISSUER, "autoplay", scopes="email profile", http=idp)
    url, flow = client.start("http://app.test/login/sso/callback")
    query = idp.authorize(url)
    assert url.startswith(ISSUER + "/authorize?") and query["scope"] == "openid email profile"
    assert query["code_challenge_method"] == "S256" and query["state"] == flow["state"]
    assert query["redirect_uri"] == "http://app.test/login/sso/callback" and len(flow["verifier"]) >= 43


def test_finish_exchanges_the_code_and_checks_the_token():
    idp = FakeIdP()
    client = OIDCClient(ISSUER, "autoplay", "s3cret", http=idp)
    url, flow = client.start("http://app.test/cb")
    query = idp.authorize(url)
    who = client.finish(flow, {"code": "good-code", "state": query["state"]})
    assert who.username == "ann@x.test" and who.groups == ["qa"]
    sent = idp.token_requests[0]
    assert sent["code_verifier"] == flow["verifier"] and sent["client_secret"] == "s3cret"


@pytest.mark.parametrize("change, params, message", [
    ({}, {"state": "forged"}, "did not match"),
    ({}, {"error": "access_denied", "error_description": "No"}, "refused: No"),
    ({}, {"code": "bad-code"}, "did not give a token"),
    ({"aud": "someone-else"}, {}, "another application"),
    ({"iss": "https://evil.test"}, {}, "another provider"),
    ({"exp": 1}, {}, "expired"),
    ({"nonce": "replayed"}, {}, "does not belong"),
    ({"email_verified": False}, {}, "not verified"),
    ({"email": None}, {}, "did not say who you are"),
])
def test_finish_refuses_what_it_cannot_trust(change, params, message):
    idp = FakeIdP()
    client = OIDCClient(ISSUER, "autoplay", http=idp)
    url, flow = client.start("http://app.test/cb")
    query = idp.authorize(url)
    if "nonce" in change:
        idp.nonce = change.pop("nonce")
    idp.claims.update(change)
    if "exp" in change:
        idp.claims["exp"] = change["exp"]
    with pytest.raises(SSOError, match=message):
        client.finish(flow, {"code": "good-code", "state": query["state"], **params})


def test_groups_can_come_from_userinfo_of_the_same_subject():
    idp = FakeIdP()
    del idp.claims["groups"]
    idp.userinfo = {"sub": "u1", "groups": ["leads"]}
    client = OIDCClient(ISSUER, "autoplay", http=idp)
    url, flow = client.start("http://app.test/cb")
    who = client.finish(flow, {"code": "good-code", "state": idp.authorize(url)["state"]})
    assert who.groups == ["leads"]
    idp.userinfo = {"sub": "someone-else", "groups": ["leads"]}
    url, flow = client.start("http://app.test/cb")
    assert client.finish(flow, {"code": "good-code", "state": idp.authorize(url)["state"]}).groups == []


def test_a_stale_flow_and_a_wrong_discovery_issuer_are_refused():
    idp = FakeIdP()
    client = OIDCClient(ISSUER, "autoplay", http=idp)
    with pytest.raises(SSOError, match="took too long"):
        client.finish({"at": 0, "state": "s"}, {"state": "s", "code": "good-code"})
    other = OIDCClient("https://other.test", "autoplay", http=idp)
    with pytest.raises(SSOError, match="another issuer"):
        other.metadata()


# ───── the API and the sign-in routes ─────

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import webapp.auth as auth_module
    import webapp.settings as settings_module
    import webapp.state as state_module
    ws = tmp_path / "workspace"
    shutil.copytree(FIXTURES, ws)
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    for key, value in {"AUTOPLAY_WORKSPACE": ws, "AUTOPLAY_DOTENV_PATH": tmp_path / "empty.env",
                       "AUTOPLAY_RUNS_DIR": tmp_path / "runs", "AUTOPLAY_AUTH_ENABLED": "true",
                       "AUTOPLAY_OIDC_ISSUER": ISSUER, "AUTOPLAY_OIDC_CLIENT_ID": "autoplay",
                       "AUTOPLAY_OIDC_LABEL": "Contoso",
                       "AUTOPLAY_OIDC_ROLE_MAP": "qa=runner, smoke-editors=SMOKE/*:editor"}.items():
        monkeypatch.setenv(key, str(value))
    settings_module.get_settings.cache_clear()
    state_module.services.cache_clear()
    svc = state_module.services()
    svc.users.add("ann", PASSWORD, "admin")
    svc.users.add("vic", PASSWORD, "viewer")
    svc.users.add("rob", PASSWORD, "runner")
    svc.users.grant("vic", "runner", "SMOKE", "dev")
    svc.users.grant("rob", "viewer", env="dev")
    monkeypatch.setattr(svc.runs, "_execute", lambda run: None)
    idp = FakeIdP()
    monkeypatch.setattr(auth_module, "sso_client",
                        lambda settings: OIDCClient(ISSUER, "autoplay", http=idp))
    from webapp.api import router
    from webapp.auth import install_auth
    app = FastAPI()
    install_auth(app, svc.settings)
    app.include_router(router)
    yield TestClient(app), svc, idp
    settings_module.get_settings.cache_clear()
    state_module.services.cache_clear()


def token(svc, name):
    return {"Authorization": f"Bearer {svc.users.new_token(name)}"}


def test_runs_are_allowed_by_the_role_for_their_system_and_environment(app_client):
    client, svc, _ = app_client
    body = {"system": "SMOKE", "env": "dev", "tags": ["smoke"], "browsers": ["chromium"]}
    assert client.post("/api/runs", json=body, headers=token(svc, "vic")).status_code == 201  # runner on SMOKE/dev
    denied = client.post("/api/runs", json=body, headers=token(svc, "rob"))  # a runner, but a viewer on dev
    assert denied.status_code == 403 and denied.json()["detail"] == \
        "Your role (viewer in SMOKE / dev) can't start or cancel runs in SMOKE / dev."
    page, sha = svc.pom.load("SMOKE", "SmokeHome.json")
    put = client.put("/api/pages/SMOKE/SmokeHome.json", json={"data": page, "hash": sha}, headers=token(svc, "vic"))
    assert put.status_code == 403  # vic is a runner on SMOKE/dev, a viewer elsewhere: no editing
    me = client.get("/api/me", headers=token(svc, "vic")).json()
    assert me == {"username": "vic", "role": "viewer", "grants": [{"system": "SMOKE", "env": "dev",
                                                                    "role": "runner"}], "sso": False}
    denials = [e for e in svc.audit.recent() if e["action"] == "denied"]
    assert any(e.get("system") == "SMOKE" and e.get("env") == "dev" for e in denials)


def test_single_sign_on_signs_in_and_maps_groups_to_roles(app_client):
    client, svc, idp = app_client
    page = client.get("/login").text
    assert "Sign in with Contoso" in page and 'name="password"' in page
    started = client.get("/login/sso?next=/history", follow_redirects=False)
    assert started.status_code == 303 and started.headers["location"].startswith(ISSUER + "/authorize?")
    query = idp.authorize(started.headers["location"])
    assert query["redirect_uri"] == "http://testserver/login/sso/callback"
    idp.claims.update(email="sam@x.test", groups=["qa", "smoke-editors"])
    back = client.get(f"/login/sso/callback?code=good-code&state={query['state']}", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/history"
    me = client.get("/api/me").json()
    assert me["username"] == "sam@x.test" and me["role"] == "runner" and me["sso"]
    assert me["grants"] == [{"system": "SMOKE", "env": "*", "role": "editor"}]
    login = next(e for e in svc.audit.recent() if e["action"] == "login")
    assert login["user"] == "sam@x.test" and login["via"] == "sso"


def test_single_sign_on_failures_show_on_the_sign_in_page(app_client):
    client, svc, idp = app_client
    started = client.get("/login/sso", follow_redirects=False)
    query = idp.authorize(started.headers["location"])
    idp.claims.update(email="nobody@x.test", groups=["unmapped"])
    page = client.get(f"/login/sso/callback?code=good-code&state={query['state']}")
    assert "no AutoPlay role" in page.text and client.get("/api/me").status_code == 401
    replay = client.get(f"/login/sso/callback?code=good-code&state={query['state']}")
    assert "took too long or was started elsewhere" in replay.text  # the flow is used once
    assert any(e["action"] == "login.failed" and e.get("via") == "sso" for e in svc.audit.recent())


def test_sso_only_turns_password_sign_in_off(app_client, monkeypatch):
    client, svc, _ = app_client
    monkeypatch.setattr(svc.settings, "oidc_only", True)
    page = client.get("/login").text
    assert "Sign in with Contoso" in page and 'name="password"' not in page
    refused = client.post("/login", data={"username": "ann", "password": PASSWORD})
    assert "Sign in with Contoso" in refused.text and client.get("/api/me").status_code == 401


def test_sso_settings_problems_are_named():
    from webapp.auth import sso_problem
    from webapp.settings import Settings
    assert "https://" in sso_problem(Settings(oidc_issuer="http://idp.test", oidc_client_id="a"))
    assert sso_problem(Settings(oidc_issuer="https://idp.test", oidc_client_id="a")) == ""
    assert "Role map" in sso_problem(Settings(oidc_issuer="https://idp.test", oidc_client_id="a",
                                              oidc_role_map="qa=root"))
