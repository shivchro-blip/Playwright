"""Request checks for the web app, which has no login yet (see Settings.auth_enabled).

- Host allowlist: blocks DNS rebinding, where a web page on another domain resolves to 127.0.0.1
  and calls the API as if it were same-origin.
- Origin check: a browser request from another site (or a sandboxed frame, Origin "null") is refused,
  for the REST API and the NiceGUI websocket alike. Scripts and curl send no Origin and are unaffected.
- Run artifacts (/runs): served with nosniff; Allure attachments and raw results (DOM snapshots of the
  tested sites) are sandboxed so their scripts can't run with this app's origin. So is the built-in
  report.html, which has no scripts and needs none.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from webapp.settings import Settings

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
WILDCARD_HOSTS = ("0.0.0.0", "::", "")
SANDBOXED_RUN_PATHS = ("/allure-report/data/attachments/", "/allure-results/", "/report.html")


def extra_hosts(settings: Settings) -> list[str]:
    return [h.strip().lower() for h in settings.allowed_hosts.split(",") if h.strip()]


def allowed_hosts(settings: Settings) -> list[str]:
    """Host names the app answers to. ["*"] only for a wildcard bind with no AUTOMATENY_ALLOWED_HOSTS."""
    extra = extra_hosts(settings)
    if settings.host in WILDCARD_HOSTS and not extra:
        return ["*"]
    hosts = list(LOOPBACK_HOSTS) + extra
    if settings.host not in WILDCARD_HOSTS and settings.host not in hosts:
        hosts.append(settings.host)
    return hosts


class OriginCheckMiddleware:
    """Refuse HTTP and websocket requests whose Origin is not this app (same host:port as the request)
    or one of the extra AUTOMATENY_ALLOWED_HOSTS (e.g. a reverse proxy that rewrites Host)."""

    def __init__(self, app: ASGIApp, hosts: list[str]):
        self.app = app
        self.hosts = set(hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and not self._allowed(origin, headers.get("host", "")):
                logger.warning("Refused %s %s from origin %r", scope["type"], scope.get("path"), origin)
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await PlainTextResponse("Cross-origin request refused", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _allowed(self, origin: str, host: str) -> bool:
        parts = urlsplit(origin)
        if not parts.scheme or not parts.netloc:  # "null" (sandboxed frame, file://) or malformed
            return False
        return parts.netloc.lower() == host.lower() or (parts.hostname or "") in self.hosts


class RunArtifactHeadersMiddleware:
    """Security headers for files under /runs (reports, console logs, raw results)."""

    def __init__(self, app: ASGIApp, prefix: str = "/runs/"):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        sandbox = any(p in path for p in SANDBOXED_RUN_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                if sandbox:
                    headers.append((b"content-security-policy", b"sandbox"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def install_security(app, settings: Settings) -> None:
    """Add the middleware to the FastAPI/NiceGUI app. Call before the app starts."""
    hosts = allowed_hosts(settings)
    if settings.host not in LOOPBACK_HOSTS:
        logger.warning("Listening on %r: the web app has no login, so anyone who can reach this address can "
                       "edit tests and start runs. Allowed hosts: %s", settings.host, ", ".join(hosts))
    # Added last = runs first: reject foreign hosts before anything else
    app.add_middleware(RunArtifactHeadersMiddleware)
    app.add_middleware(OriginCheckMiddleware, hosts=extra_hosts(settings))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
