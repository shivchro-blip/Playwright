"""Single sign-on end to end: the real app, a browser and a small fake OpenID Connect provider. The provider's groups
give the person a role everywhere and a grant for one system and environment, and no local admin is needed."""
from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_ui import running_app


def _jwt(claims: dict) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'none'})}.{enc(claims)}."


def fake_idp(groups: list[str]):
    """Signs everyone in as sam@x.test with `groups`, without asking (the person "is" signed in there)."""
    state: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _json(self, body: dict, status: int = 200) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 - http.server's naming
            url = urlsplit(self.path)
            if url.path == "/.well-known/openid-configuration":
                self._json({"issuer": state["issuer"], "authorization_endpoint": state["issuer"] + "/authorize",
                            "token_endpoint": state["issuer"] + "/token"})
            elif url.path == "/authorize":
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                state["nonce"], state["client_id"] = q["nonce"], q["client_id"]
                self.send_response(302)
                self.send_header("Location", q["redirect_uri"] + "?" + urlencode({"code": "c0de",
                                                                                  "state": q["state"]}))
                self.end_headers()
            else:
                self._json({}, 404)

        def do_POST(self):  # noqa: N802
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            if self.path != "/token" or form.get("code") != ["c0de"] or not form.get("code_verifier"):
                self._json({"error": "invalid_grant"}, 400)
                return
            self._json({"id_token": _jwt({"iss": state["issuer"], "aud": state["client_id"], "sub": "u-sam",
                                          "email": "sam@x.test", "email_verified": True, "groups": groups,
                                          "nonce": state["nonce"], "exp": time.time() + 300})})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["issuer"] = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state["issuer"]


def test_sign_in_with_the_identity_provider_and_roles_from_its_groups(workspace: Path, tmp_path: Path):
    server, issuer = fake_idp(["qa", "smoke-runners"])
    problems: list[str] = []
    try:
        with running_app(tmp_path, AUTOPLAY_AUTH_ENABLED="true", AUTOPLAY_OIDC_ISSUER=issuer,
                         AUTOPLAY_OIDC_CLIENT_ID="autoplay", AUTOPLAY_OIDC_LABEL="Fake IdP",
                         AUTOPLAY_OIDC_ROLE_MAP="leads=admin, qa=viewer, smoke-runners=SMOKE/dev:runner",
                         NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost") as url, \
                sync_playwright() as p:
            # no local admin exists: the "leads" group is mapped to admin, so the app starts
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda e: problems.append(str(e)))
            page.goto(url, wait_until="networkidle")
            expect(page).to_have_url(url + "login?next=/")
            page.get_by_test_id("sso").click()  # off to the provider and straight back
            expect(page.get_by_test_id("signed-in")).to_contain_text("sam@x.test")
            expect(page).to_have_url(url)
            expect(page.get_by_test_id("signed-in-role")).to_have_text("viewer +1")
            me = page.request.get(url + "api/v1/me").json()
            assert me["role"] == "viewer" and me["sso"] is True
            assert me["grants"] == [{"system": "SMOKE", "env": "dev", "role": "runner"}]
            browser.close()
    finally:
        server.shutdown()
    assert problems == []
    users = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))["users"]
    assert [(u["username"], u["role"], u["sso"], u["password_hash"]) for u in users] == \
        [("sam@x.test", "viewer", True, "")]
