"""Sign-in, roles and the audit trail for the web app.

- Login is off for the local single-user app; on when AUTOMATENY_AUTH_ENABLED=true or the app listens on a
  network address (Settings.login_required). Without login every action is done by "local" (admin).
- Browsers sign in on /login (a plain form POST, so no password goes over the UI websocket); the user name is
  kept in a signed, HttpOnly, SameSite=Lax session cookie that expires after SESSION_HOURS.
- Scripts and CI use API tokens:  Authorization: Bearer any_...  (python -m webapp.users token NAME).
- Every change (runs, tests, pages, schedules, settings) and every refused attempt goes to the audit log.
"""
from __future__ import annotations

import html
import logging
import time
from collections import defaultdict, deque
from urllib.parse import quote, urlsplit

from fastapi import Depends, HTTPException, Request
from nicegui import ui
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from webapp.services.users import LOCAL_USER, ROLE_LABELS, User, session_secret
from webapp.settings import Settings
from webapp.state import services

logger = logging.getLogger(__name__)

SESSION_HOURS = 12
PUBLIC_PATHS = ("/login", "/logout", "/favicon.ico")
PUBLIC_PREFIXES = ("/_nicegui", "/assets/")  # framework and app assets (no data), and the websocket
MAX_FAILURES, FAILURE_WINDOW = 5, 300  # sign-in attempts per user name / client per 5 minutes
_failures: dict[str, deque] = defaultdict(deque)

ACTIONS = {  # permission -> what the refusal message says
    "run": "start or cancel runs", "check": "check locators", "edit": "change test cases or pages",
    "record": "record tests", "schedule": "change schedules", "delete": "delete runs", "settings": "change settings",
    "ai.read": "use AI triage and summaries", "ai.write": "apply AI suggestions", "users": "manage users",
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


def guard(permission: str, action: str = "", target: str = "") -> bool:
    """UI: may the current user do this? Refusals are shown and audited."""
    user = ui_user()
    if user and user.can(permission):
        return True
    role = user.role if user else "signed out"
    ui.notify(f"Your role ({role}) can't {ACTIONS.get(permission, permission)}.", type="warning")
    audit("denied", target, user=user, permission=permission, attempted=action)
    return False


def require(permission: str):
    """API dependency: the request's user, who must have `permission` (401/403 otherwise)."""
    def dependency(request: Request) -> User:
        user = request_user(request)
        if user is None:
            raise HTTPException(401, "Sign in, or send an API token: Authorization: Bearer <token>")
        if not user.can(permission):
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
    message = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · AutomateNY</title>
<link rel="stylesheet" href="/assets/login.css"></head><body><main>
<form method="post" action="/login" aria-labelledby="t">
<div class="brand"><span class="mark" aria-hidden="true"></span>AutomateNY</div>
<h1 id="t">Sign in</h1>{message}
<label for="u">User name</label><input id="u" name="username" autocomplete="username" required autofocus>
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<input type="hidden" name="next" value="{html.escape(_safe_next(next_path))}">
<button type="submit">Sign in</button>
<p class="hint">Accounts are managed with <code>python -m webapp.users</code>.</p></form></main></body></html>""",
                        headers={"Cache-Control": "no-store"})


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
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="automateny_session",
                       max_age=SESSION_HOURS * 3600, same_site="lax", https_only=False)
    return secret


def role_summary(user: User | None) -> str:
    return ROLE_LABELS.get(user.role, "") if user else ""
