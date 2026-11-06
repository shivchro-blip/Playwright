"""The control plane's web app: the portfolio of tenants, adding and running them, and its API (/cp/api)."""
from __future__ import annotations

import html
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from nicegui import app, ui
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from webapp.control.portfolio import rollup
from webapp.control.service import control
from webapp.control.tenants import TenantError
from webapp.services.users import LOCAL_USER, User, session_secret
from webapp.ui.components import text
from webapp.ui.theme import add_head, apply_brand, icon, register_assets

SESSION_HOURS = 12
PUBLIC = ("/login", "/logout", "/favicon.ico")
PUBLIC_PREFIXES = ("/_nicegui", "/assets/")
HEALTH_PILL = {"red": ("failed", "Needs attention"), "amber": ("warn", "Warning"), "green": ("passed", "Healthy"),
               "unknown": ("idle", "Unknown")}


# ───────────── who is acting ─────────────

def _session_user(session: dict) -> User | None:
    cp = control()
    if not cp.settings.login_required:
        return LOCAL_USER
    name, at = session.get("user"), session.get("at", 0)
    if not name or time.time() - float(at) > SESSION_HOURS * 3600:
        return None
    user = cp.users.get(name)
    return user if user and not user.disabled else None


def request_user(request: Request) -> User | None:
    cp = control()
    if not cp.settings.login_required:
        return LOCAL_USER
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return cp.users.by_token(auth[7:].strip())
    return _session_user(request.session) if "session" in request.scope else None


def page_user() -> User | None:
    try:
        request = ui.context.client.request
    except (AttributeError, RuntimeError):
        return None
    return request_user(request) if request is not None else None


def require_admin(request: Request) -> User:
    user = request_user(request)
    if user is None:
        raise HTTPException(401, "Sign in, or send a token: Authorization: Bearer <token>")
    if user.role != "admin":
        control().audit.record(user.username, "denied", request.url.path)
        raise HTTPException(403, "Only control plane admins can change tenants.")
    return user


def require_user(request: Request) -> User:
    user = request_user(request)
    if user is None:
        raise HTTPException(401, "Sign in, or send a token: Authorization: Bearer <token>")
    return user


class AuthMiddleware:
    def __init__(self, inner: ASGIApp):
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not control().settings.login_required or path in PUBLIC \
                or path.startswith(PUBLIC_PREFIXES):
            await self.inner(scope, receive, send)
            return
        if request_user(Request(scope, receive)) is None:
            response = JSONResponse({"detail": "Sign in"}, status_code=401) if path.startswith("/cp/api/") \
                else RedirectResponse(f"/login?next={quote(path)}", status_code=303)
            await response(scope, receive, send)
            return
        await self.inner(scope, receive, send)


def _login_page(error: str = "", next_path: str = "/") -> HTMLResponse:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · AutoPlay control plane</title>
<link rel="stylesheet" href="/assets/login.css"></head><body><main>
<form method="post" action="/login" aria-labelledby="t"><h1 id="t">AutoPlay control plane</h1>{err}
<input type="hidden" name="next" value="{html.escape(next_path)}">
<label for="u">User name</label><input id="u" name="username" autocomplete="username" required autofocus>
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button></form></main></body></html>""")


def install_auth() -> str:
    cp = control()
    secret = session_secret(cp.settings.dir / ".session-key")
    app.add_middleware(AuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=secret, max_age=SESSION_HOURS * 3600, same_site="lax",
                       https_only=False, session_cookie="autoplay_cp")

    @app.get("/login", include_in_schema=False)
    async def login_form(next: str = "/"):  # noqa: A002
        return _login_page(next_path=next if next.startswith("/") and not next.startswith("//") else "/")

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request):
        form = await request.form()
        username, password = str(form.get("username", "")), str(form.get("password", ""))
        target = str(form.get("next", "/"))
        target = target if target.startswith("/") and not target.startswith("//") else "/"
        user = cp.users.authenticate(username, password)
        if user is None:
            cp.audit.record(username or "?", "login.failed")
            return _login_page("User name or password is wrong.", target)
        request.session.clear()
        request.session.update({"user": user.username, "at": time.time()})
        cp.audit.record(user.username, "login")
        return RedirectResponse(target, status_code=303)

    @app.get("/logout", include_in_schema=False)
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return secret


# ───────────── API ─────────────

router = APIRouter(prefix="/cp/api")


def _view(v) -> dict:
    out = v.to_dict()
    if v.tenant.managed:
        out["process"] = control().supervisor.state(v.tenant.id)
    return out


@router.get("/portfolio")
def portfolio(fresh: bool = False, _: User = Depends(require_user)) -> dict:
    """Every tenant's health and summary, and the totals and attention list across them."""
    views = control().portfolio.collect(fresh=fresh)
    return {"rollup": rollup(views), "tenants": [_view(v) for v in views]}


@router.get("/tenants")
def list_tenants(_: User = Depends(require_user)) -> list[dict]:
    cp = control()
    return [{**t.to_dict(), "has_token": bool(cp.tenants.token(t)),
             **({"process": cp.supervisor.state(t.id)} if t.managed else {})} for t in cp.tenants.list()]


class CreateTenant(BaseModel):
    name: str
    admin: str = ""  # create a managed tenant with this first admin...
    password: str = ""
    template: str = ""
    port: int = 0
    url: str = ""  # ...or register an instance that runs elsewhere
    token: str = ""
    owner: str = ""
    description: str = ""


@router.post("/tenants", status_code=201)
def create_tenant(req: CreateTenant, user: User = Depends(require_admin)) -> dict:
    cp = control()
    try:
        if req.url:
            tenant, password = cp.register(req.name, req.url, req.token, owner=req.owner,
                                           description=req.description), ""
        else:
            tenant, password = cp.create(req.name, req.admin, owner=req.owner, description=req.description,
                                         password=req.password, template=req.template, port=req.port)
            cp.supervisor.start(tenant)
    except (TenantError, ValueError, OSError) as e:
        raise HTTPException(400, str(e)) from e
    cp.audit.record(user.username, "tenant.create" if tenant.managed else "tenant.register", tenant.id)
    return {"tenant": tenant.to_dict(), **({"admin_password": password} if password and not req.password else {})}


@router.delete("/tenants/{tid}")
def delete_tenant(tid: str, user: User = Depends(require_admin)) -> dict:
    cp = control()
    if cp.remove(tid) is None:
        raise HTTPException(404, "No such tenant")
    cp.audit.record(user.username, "tenant.remove", tid)
    return {"removed": tid, "note": "Its workspace and runs are kept on disk."}


@router.post("/tenants/{tid}/{action}")
def tenant_action(tid: str, action: str, user: User = Depends(require_admin)) -> dict:
    cp = control()
    if action not in ("start", "stop", "restart"):
        raise HTTPException(404, "Use start, stop or restart.")
    try:
        if action == "restart":
            cp.restart(tid)
        else:
            cp.set_running(tid, action == "start")
    except TenantError as e:
        raise HTTPException(404 if "No such" in str(e) else 400, str(e)) from e
    cp.audit.record(user.username, f"tenant.{action}", tid)
    return cp.supervisor.state(tid)


# ───────────── the page ─────────────

def _pill(state: str, testid: str = "") -> None:
    kind, label = HEALTH_PILL.get(state, ("idle", state))
    with ui.element("span").classes(f"any-pill pill-{kind}").props(f'data-testid="{testid}"' if testid else ""):
        ui.element("span").classes("dot").props('aria-hidden="true"')
        text("span", label)


class PortfolioPage:
    def __init__(self, user: User):
        self.cp, self.user = control(), user
        self.signature = None
        with ui.header().classes(replace="any-nav"):
            with ui.element("div").classes("any-brand"):
                with ui.element("div").classes("any-brand-mark"):
                    ui.icon(icon("play-solid")).props('aria-hidden="true"')
                ui.label("AutoPlay control plane")
            ui.element("div").classes("flex-grow")
            if self.cp.settings.login_required:
                with ui.element("div").classes("any-user"):
                    text("span", user.username, "any-user-name")
                    text("span", user.role, "any-tag")
                    with ui.element("a").classes("any-signout").props('href="/logout"'):
                        ui.label("Sign out")
        with ui.element("div").classes("any-page-inner is-wide any-cp"):
            with ui.element("section").classes("any-card").props('aria-label="Tenants"'):
                with ui.element("div").classes("any-card-head"):
                    with ui.element("div").classes("any-head-left"):
                        text("h2", "Tenants", "any-title")
                        self.count = text("span", "", "any-count-pill")
                    with ui.element("div").classes("any-head-right"):
                        ui.button("Refresh", icon=icon("rotate-cw"), color=None,
                                  on_click=lambda: self.body.refresh(True)) \
                            .props('flat no-caps data-testid="cp-refresh"').classes("any-btn")
                        if user.role == "admin":
                            ui.button("New tenant", icon=icon("plus"), color=None, on_click=self.add) \
                                .props('data-testid="cp-new"').classes("any-primary")
                with ui.element("div").classes("any-jr-intro"):
                    ui.label("Each tenant is its own AutoPlay: its own pages, tests, journeys, runs, users and "
                             "logins, in its own process. This page reads each one's summary with a read-only "
                             "service account.").classes("any-muted text-sm")
                self.body()
        ui.timer(15.0, lambda: self.body.refresh(True))

    @ui.refreshable_method
    def body(self, fresh: bool = False) -> None:
        views = self.cp.portfolio.collect(fresh=fresh)
        total = rollup(views)
        self.count.text = f"{len(views)} tenant{'s' if len(views) != 1 else ''}"
        with ui.element("div").classes("any-jr-summary").props('data-testid="cp-rollup"'):
            for state in ("red", "amber", "green", "unknown"):
                n = total["health"].get(state, 0)
                if n:
                    text("span", f"{n} {HEALTH_PILL[state][1].lower()}", f"any-pill pill-{HEALTH_PILL[state][0]}")
            text("span", f"{total['journeys_failing']} failing journey statuses", "any-jr-env")
            text("span", f"{total['monitors_down']} monitors down", "any-jr-env")
            text("span", f"{total['runs_active']} running, {total['runs_queued']} queued", "any-jr-env")
        if not views:
            ui.label("No tenants yet. Create one with New tenant (a workspace, its first admin and a port), or "
                     "register an AutoPlay that already runs elsewhere.").classes("any-empty any-muted any-jr-pad") \
                .props('data-testid="cp-empty"')
            return
        if total["attention"]:
            text("h3", "Needs attention", "any-eyebrow any-jr-pad")
            with ui.element("ul").classes("any-cp-attention").props('data-testid="cp-attention"'):
                for a in total["attention"][:15]:
                    with ui.element("li"):
                        text("strong", a["tenant"])
                        text("span", " " + a["what"])
        with ui.element("div").classes("any-table-wrap"):
            with ui.element("table").classes("any-table w-full").props('data-testid="cp-table"'):
                with ui.element("thead"):
                    with ui.element("tr"):
                        for head in ("Tenant", "Health", "Journeys", "Latest release", "Monitors", "Runs",
                                     "Runners", ""):
                            text("th", head)
                with ui.element("tbody"):
                    for v in views:
                        self._row(v)

    def _row(self, v) -> None:
        t, s = v.tenant, v.summary or {}
        with ui.element("tr").props(f'data-testid="cp-row-{t.id}"'):
            with ui.element("td"):
                text("div", t.name, "any-tc-name")
                text("div", " · ".join(x for x in (t.owner, t.url) if x), "any-tc-code")
            with ui.element("td"):
                _pill(v.health, f"cp-health-{t.id}")
                if v.error:
                    text("div", v.error, "any-muted text-xs")
                elif v.reasons:
                    text("div", v.reasons[0] + (f" (+{len(v.reasons) - 1})" if len(v.reasons) > 1 else ""),
                         "any-muted text-xs")
            totals = (s.get("journeys") or {}).get("total") or {}
            text("td", f"{totals.get('passing', 0)} passing, {totals.get('failing', 0)} failing" if s else "-",
                 "any-small").props(f'data-testid="cp-journeys-{t.id}"')
            release = (s.get("releases") or [None])[0] if s else None
            text("td", f"{release['name']}: {release['decision']}" if release else "-", "any-small")
            mons = s.get("monitors") or []
            text("td", (f"{sum(m['up'] for m in mons)} up, {sum(m['down'] for m in mons)} down" if mons else "-"),
                 "any-small").props(f'data-testid="cp-monitors-{t.id}"')
            runs = s.get("runs") or {}
            day = runs.get("last_24h") or {}
            text("td", (f"{runs.get('active', 0)} running · 24h: {day.get('passed', 0)} passed, "
                        f"{day.get('failed', 0)} failed") if s else "-", "any-small")
            runners = s.get("runners") or {}
            text("td", f"{runners.get('online', 0)} of {runners.get('total', 0)} online" if s else "-", "any-small")
            with ui.element("td").classes("text-right"):
                if t.managed:
                    state = self.cp.supervisor.state(t.id)
                    text("span", state["process"], "any-tag").props(f'data-testid="cp-process-{t.id}"')
                    if self.user.role == "admin":
                        action = "stop" if t.running else "start"
                        ui.button(action.capitalize(), color=None,
                                  on_click=lambda t=t, a=action: self.action(t.id, a)) \
                            .props(f'flat dense no-caps data-testid="cp-{action}-{t.id}"').classes("any-btn")
                with ui.element("a").classes("any-btn any-cp-open").props(
                        f'href="{t.url}" target="_blank" rel="noopener" data-testid="cp-open-{t.id}"'):
                    ui.label("Open")
                    ui.icon(icon("external-link")).props('aria-hidden="true"')

    def action(self, tid: str, action: str) -> None:
        try:
            self.cp.set_running(tid, action == "start")
        except TenantError as e:
            ui.notify(str(e), type="negative")
            return
        self.cp.audit.record(self.user.username, f"tenant.{action}", tid)
        ui.notify(f"{'Starting' if action == 'start' else 'Stopped'} {tid}", type="positive")
        self.body.refresh(True)

    async def add(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("any-app any-dialog w-[36rem]").props(
                'role="dialog" aria-label="New tenant"'):
            text("h2", "New tenant", "any-title")
            mode = ui.toggle({"create": "Create one here", "register": "Register one that runs elsewhere"},
                             value="create").props('no-caps data-testid="cp-mode"')
            name = ui.input("Name", placeholder="Licensing team").props('data-testid="cp-name"').classes("w-full")
            owner = ui.input("Owner", placeholder="Team or person").props('data-testid="cp-owner"') \
                .classes("w-full")
            with ui.element("div").classes("w-full") as create_box:
                admin = ui.input("First admin's user name", placeholder="lead") \
                    .props('data-testid="cp-admin"').classes("w-full")
                template = ui.input("Copy pages and tests from (optional)",
                                    placeholder="a workspace folder, e.g. C:\\autoplay\\workspace") \
                    .props('data-testid="cp-template"').classes("w-full")
                ui.label("Gets its own workspace, users, logins (.env) and port, and starts now. Runs, users and "
                         "secrets are never copied.").classes("any-muted text-xs")
            with ui.element("div").classes("w-full") as register_box:
                url = ui.input("Address", placeholder="https://autoplay.team-b.example.com") \
                    .props('data-testid="cp-url"').classes("w-full")
                token = ui.input("API token of a viewer account there", password=True) \
                    .props('data-testid="cp-token"').classes("w-full")
            create_box.bind_visibility_from(mode, "value", value="create")
            register_box.bind_visibility_from(mode, "value", value="register")
            with ui.element("div").classes("any-dialog-actions"):
                ui.button("Cancel", color=None, on_click=lambda: dialog.submit(None)).classes("any-btn is-38")
                ui.button("Save", color=None, on_click=lambda: dialog.submit("save")) \
                    .props('data-testid="cp-save"').classes("any-primary")
        choice = await dialog
        dialog.delete()
        if choice != "save":
            return
        if self.user.role != "admin":
            ui.notify("Only control plane admins can add tenants.", type="warning")
            return
        try:
            if mode.value == "register":
                tenant = self.cp.register(name.value or "", url.value or "", (token.value or "").strip(),
                                          owner=owner.value or "")
                password = ""
            else:
                tenant, password = self.cp.create(name.value or "", (admin.value or "").strip(),
                                                  owner=owner.value or "", template=(template.value or "").strip())
                self.cp.supervisor.start(tenant)
        except (TenantError, ValueError, OSError) as e:
            ui.notify(str(e), type="negative", multi_line=True)
            return
        self.cp.audit.record(self.user.username, "tenant.create" if tenant.managed else "tenant.register",
                             tenant.id)
        if password:
            with ui.dialog() as done, ui.card().classes("any-app any-dialog w-[32rem]").props(
                    'role="dialog" aria-label="Tenant created" data-testid="cp-created"'):
                text("h2", f"{tenant.name} is starting", "any-title")
                ui.label(f"Open {tenant.url} and sign in as {admin.value} with this password. It is shown once; "
                         "change it after signing in.").classes("text-sm")
                text("div", password, "mono any-cp-secret").props('data-testid="cp-password"')
                ui.button("Done", color=None, on_click=done.close).classes("any-primary")
            done.open()
        else:
            ui.notify(f"Registered {tenant.name}", type="positive")
        self.body.refresh(True)


def register_pages() -> None:
    register_assets()

    @ui.page("/")
    def index() -> None:
        ui.page_title("AutoPlay control plane")
        add_head()
        apply_brand()
        ui.query(".nicegui-content").classes("any-content")
        user = page_user() or LOCAL_USER
        PortfolioPage(user)


def serve() -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cp = control()
    s = cp.settings
    if s.login_required and not any(u.role == "admin" and not u.disabled for u in cp.users.list()):
        raise SystemExit(f"Sign-in is required (host {s.host!r} or AUTOPLAY_CP_AUTH_ENABLED) but the control plane "
                         f"has no admin in {s.dir / 'users.json'}. Create one with:\n"
                         "  python -m webapp.control user add <name> --role admin")
    secret = install_auth()
    app.include_router(router)
    register_pages()
    if s.supervise:
        cp.supervisor.run()
        app.on_shutdown(cp.supervisor.stop_all)
    ui.run(host=s.host, port=s.port, title="AutoPlay control plane", reload=False, show=False,
           fastapi_docs=True, show_welcome_message=True, storage_secret=secret)
