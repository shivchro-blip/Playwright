"""Sign-in, roles and the audit trail for the web app.

- Login is off for the local single-user app; on when AUTOPLAY_AUTH_ENABLED=true or the app listens on a
  network address (Settings.login_required). Without login every action is done by "local" (admin).
- Browsers sign in on /login (a plain form POST, so no password goes over the UI websocket); the user name is
  kept in a signed, HttpOnly, SameSite=Lax session cookie that expires after SESSION_HOURS.
- Scripts and CI use API tokens:  Authorization: Bearer any_...  (python -m webapp.users token NAME).
- Single sign-on (OpenID Connect) when AUTOPLAY_OIDC_ISSUER and AUTOPLAY_OIDC_CLIENT_ID are set: /login/sso and
  its callback (webapp/services/oidc.py); the provider's groups can set roles, also per system and environment.
- A role can differ per system and environment (User.grants): guard() and authorize() take the system and
  environment an action is for.
- Every change (runs, tests, pages, schedules, settings) and every refused attempt goes to the audit log.
"""
from __future__ import annotations

import html
import logging
import time
from collections import defaultdict, deque
from urllib.parse import quote, urlsplit

from fastapi import Depends, HTTPException, Request
from nicegui import run, ui
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from webapp.services.oidc import OIDCClient, RoleMap, SSOError
from webapp.services.users import LOCAL_USER, ROLE_LABELS, User, describe_grant, session_secret
from webapp.settings import Settings
from webapp.state import services

logger = logging.getLogger(__name__)

SESSION_HOURS = 12
PUBLIC_PATHS = ("/login", "/logout", "/favicon.ico", "/login/sso", "/login/sso/callback")
PUBLIC_PREFIXES = ("/_nicegui", "/assets/",   # framework and app assets (no data), and the websocket
                   "/runner-api/")  # remote runners: each call is checked against its runner token instead
MAX_FAILURES, FAILURE_WINDOW = 5, 300  # sign-in attempts per user name / client per 5 minutes
_failures: dict[str, deque] = defaultdict(deque)

ACTIONS = {  # permission -> what the refusal message says
    "run": "start or cancel runs", "check": "check locators", "edit": "change test cases or pages",
    "record": "record tests", "schedule": "change schedules", "delete": "delete runs", "settings": "change settings",
    "ai.read": "use AI triage and summaries", "ai.write": "apply AI suggestions", "users": "manage users",
    "signoff": "sign off a release",
}


# ───────────── who is acting ─────────────

def _session_user(session: dict) -> User | None:
    svc = services()
    if not svc.settings.login_required:
        return LOCAL_USER
    name, at = session.get("user"), session.get("at", 0)
    if not name or time.time() - float(at) > SESSION_HOURS * 3600:
        return None
    user = svc.users.get(name)
    return user if user and not user.disabled else None


def request_user(request: Request) -> User | None:
    svc = services()
    if not svc.settings.login_required:
        return LOCAL_USER
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return svc.users.by_token(auth[7:].strip())
    return _session_user(request.session) if "session" in request.scope else None


def ui_user() -> User | None:
    """The user of the current page (UI event handlers)."""
    if not services().settings.login_required:
        return LOCAL_USER
    try:
        request = ui.context.client.request
    except (AttributeError, RuntimeError):
        return None
    return _session_user(request.session) if request is not None and "session" in request.scope else None


def audit(action: str, target: str = "", user: User | None = None, **details) -> None:
    actor = user or ui_user()
    services().audit.record(actor.username if actor else "anonymous", action, target, **details)


def _where(system: str | None, env: str | None) -> str:
    return " / ".join(x for x in (system, env) if x)


def refusal(user: User | None, permission: str, system: str | None = None, env: str | None = None) -> str:
    if user is None:
        return f"Sign in to {ACTIONS.get(permission, permission)}."
    where = _where(system, env) if user.grants else ""  # the place only matters to someone with grants
    role = user.role_for(system, env)
    return f"Your role ({role}{' in ' + where if where else ''}) can't {ACTIONS.get(permission, permission)}" \
           + (f" in {where}." if where else ".")


def guard(permission: str, action: str = "", target: str = "", *, system: str | None = None,
          env: str | None = None) -> bool:
    """UI: may the current user do this (in this system and environment, when the action is for one)? Refusals
    are shown and audited."""
    user = ui_user()
    if user and user.can(permission, system, env):
        return True
    ui.notify(refusal(user, permission, system, env), type="warning")
    audit("denied", target, user=user, permission=permission, attempted=action,
          **{k: v for k, v in (("system", system), ("env", env)) if v})
    return False


def authorize(user: User, permission: str, system: str | None = None, env: str | None = None,
              path: str = "") -> None:
    """API: 403 unless the user may do this in this system and environment (after require() let them in)."""
    if not user.can(permission, system, env):
        services().audit.record(user.username, "denied", path, permission=permission,
                                **{k: v for k, v in (("system", system), ("env", env)) if v})
        raise HTTPException(403, refusal(user, permission, system, env))


def require_user():
    """API dependency: the request's user, whatever their role (401 when signed out)."""
    def dependency(request: Request) -> User:
        user = request_user(request)
        if user is None:
            raise HTTPException(401, "Sign in, or send an API token: Authorization: Bearer <token>")
        return user
    return Depends(dependency)


def require(permission: str, scoped: bool = False):
    """API dependency: the request's user, who must have `permission` (401/403 otherwise). With scoped=True a role
    for some system or environment is enough to get in: the endpoint then calls authorize() with the system and
    environment it acts on."""
    def dependency(request: Request) -> User:
        user = request_user(request)
        if user is None:
            raise HTTPException(401, "Sign in, or send an API token: Authorization: Bearer <token>")
        if not (user.may_somewhere(permission) if scoped else user.can(permission)):
            services().audit.record(user.username, "denied", request.url.path, permission=permission,
                                    method=request.method)
            raise HTTPException(403, f"Your role ({user.role}) can't {ACTIONS.get(permission, permission)}.")
        return user
    return Depends(dependency)


# ───────────── middleware and the sign-in page ─────────────

class AuthMiddleware:
    """Sends signed-out browsers to /login; answers 401 to API calls without a session or token."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not services().settings.login_required:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if request_user(request) is None:
            if path.startswith("/api/") or path in ("/docs", "/openapi.json"):
                response = JSONResponse({"detail": "Sign in, or send an API token: Authorization: Bearer <token>"},
                                        status_code=401)
            else:
                response = RedirectResponse(f"/login?next={quote(path)}", status_code=303)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class ApiAuditMiddleware:
    """Audit every API call that changes something: who, what, and how it ended."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS") \
                or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        status = {"code": 500}

        async def capture(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, capture)
        user = request_user(Request(scope))
        services().audit.record(user.username if user else "anonymous", f"api.{scope['method'].lower()}",
                                scope["path"], status=status["code"], via="api")


def _safe_next(target: str) -> str:
    parts = urlsplit(target or "/")
    return target if target.startswith("/") and not target.startswith("//") and not parts.netloc else "/"


def _login_page(error: str = "", next_path: str = "/") -> HTMLResponse:
    settings = services().settings
    message = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    nxt = html.escape(_safe_next(next_path))
    sso = (f'<a class="sso" href="/login/sso?next={quote(_safe_next(next_path))}" data-testid="sso">'
           f'Sign in with {html.escape(settings.oidc_label or "single sign-on")}</a>') if settings.sso_enabled else ""
    password = "" if settings.sso_enabled and settings.oidc_only else f"""
{'<p class="or">or</p>' if sso else ''}
<label for="u">User name</label>
<input id="u" name="username" autocomplete="username" required{'' if sso else ' autofocus'}>
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<input type="hidden" name="next" value="{nxt}">
<button type="submit">Sign in</button>
<p class="hint">Accounts are managed with <code>python -m webapp.users</code>.</p>"""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · AutoPlay</title>
<link rel="stylesheet" href="/assets/login.css"></head><body><main>
<form method="post" action="/login" aria-labelledby="t">
<div class="brand"><span class="mark" aria-hidden="true"></span>AutoPlay</div>
<h1 id="t">Sign in</h1>{message}{sso}{password}</form></main></body></html>""",
                        headers={"Cache-Control": "no-store"})


# ───────────── single sign-on ─────────────

_sso: dict[tuple, OIDCClient] = {}


def sso_problem(settings: Settings) -> str:
    """What is wrong with the single sign-on settings, or ""."""
    issuer = settings.oidc_issuer.strip()
    host = urlsplit(issuer).hostname or ""
    if not issuer.startswith("https://") and host not in ("127.0.0.1", "localhost", "::1"):
        return "AUTOPLAY_OIDC_ISSUER must be an https:// address."
    try:
        RoleMap.parse(settings.oidc_role_map)
    except ValueError as e:
        return str(e)
    if settings.oidc_default_role and settings.oidc_default_role not in ("viewer", "runner", "editor", "admin"):
        return "AUTOPLAY_OIDC_DEFAULT_ROLE is one of viewer, runner, editor, admin (or empty)."
    return ""


def sso_client(settings: Settings) -> OIDCClient:
    key = (settings.oidc_issuer, settings.oidc_client_id, settings.oidc_client_secret, settings.oidc_scopes,
           settings.oidc_username_claim, settings.oidc_groups_claim)
    if key not in _sso:
        _sso[key] = OIDCClient(settings.oidc_issuer, settings.oidc_client_id, settings.oidc_client_secret,
                               scopes=settings.oidc_scopes, username_claim=settings.oidc_username_claim,
                               groups_claim=settings.oidc_groups_claim)
    return _sso[key]


def _callback_url(request: Request, settings: Settings) -> str:
    base = settings.public_url.rstrip("/") if settings.public_url else str(request.base_url).rstrip("/")
    return base + "/login/sso/callback"


def _throttled(key: str) -> bool:
    attempts = _failures[key]
    while attempts and time.time() - attempts[0] > FAILURE_WINDOW:
        attempts.popleft()
    return len(attempts) >= MAX_FAILURES


def install_auth(app, settings: Settings) -> None:
    """Session cookie + sign-in routes + middleware. Call before install_security (which must run first)."""

    @app.get("/login", include_in_schema=False)
    async def login_form(request: Request, next: str = "/"):  # noqa: A002 - query parameter name
        if not settings.login_required:
            return RedirectResponse("/", status_code=303)
        return _login_page(next_path=next)

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request):
        form = await request.form()
        username, password = str(form.get("username", "")).strip(), str(form.get("password", ""))
        next_path = _safe_next(str(form.get("next", "/")))
        if settings.sso_enabled and settings.oidc_only:
            return _login_page(f"Sign in with {settings.oidc_label}.", next_path)
        client = request.client.host if request.client else "?"
        if _throttled(username.casefold()) or _throttled(client):
            services().audit.record(username or "?", "login.throttled", client)
            return _login_page("Too many attempts. Wait a few minutes and try again.", next_path)
        user = services().users.authenticate(username, password)
        if not user:
            _failures[username.casefold()].append(time.time())
            _failures[client].append(time.time())
            services().audit.record(username or "?", "login.failed", client)
            return _login_page("User name or password is wrong.", next_path)
        request.session.clear()
        request.session.update({"user": user.username, "at": time.time()})
        services().audit.record(user.username, "login", client)
        return RedirectResponse(next_path, status_code=303)

    @app.get("/login/sso", include_in_schema=False)
    async def sso_start(request: Request, next: str = "/"):  # noqa: A002 - query parameter name
        if not settings.login_required or not settings.sso_enabled:
            return RedirectResponse("/login" if settings.login_required else "/", status_code=303)
        try:
            url, flow = await run.io_bound(sso_client(settings).start, _callback_url(request, settings))
        except SSOError as e:
            return _login_page(str(e), next)
        request.session["sso"] = {**flow, "next": _safe_next(next)}
        return RedirectResponse(url, status_code=303)

    @app.get("/login/sso/callback", include_in_schema=False)
    async def sso_callback(request: Request):
        flow = request.session.pop("sso", None)
        next_path = _safe_next((flow or {}).get("next", "/"))
        client = request.client.host if request.client else "?"
        if not settings.sso_enabled:
            return RedirectResponse("/login", status_code=303)
        try:
            who = await run.io_bound(sso_client(settings).finish, flow, dict(request.query_params))
            role, grants = RoleMap.parse(settings.oidc_role_map).resolve(who.groups)
            user = services().users.sso_user(who.username, role, grants, settings.oidc_default_role)
        except (SSOError, PermissionError, ValueError) as e:
            services().audit.record("?", "login.failed", client, via="sso", reason=str(e))
            return _login_page(str(e), next_path)
        request.session.clear()
        request.session.update({"user": user.username, "at": time.time()})
        services().audit.record(user.username, "login", client, via="sso", role=user.role,
                                grants=[describe_grant(g) for g in user.grants] or None)
        return RedirectResponse(next_path, status_code=303)

    @app.get("/logout", include_in_schema=False)
    async def logout(request: Request):
        name = request.session.get("user")
        request.session.clear()
        if name:
            services().audit.record(name, "logout")
        return RedirectResponse("/login" if settings.login_required else "/", status_code=303)

    app.add_middleware(ApiAuditMiddleware)
    app.add_middleware(AuthMiddleware)
    secret = session_secret(settings.resolved_users_file.with_name(".session-secret"))
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="autoplay_session",
                       max_age=SESSION_HOURS * 3600, same_site="lax", https_only=False)
    return secret


def role_summary(user: User | None) -> str:
    if not user:
        return ""
    label = ROLE_LABELS.get(user.role, "")
    return label + ("; " + "; ".join(describe_grant(g) for g in user.grants) if user.grants else "")
