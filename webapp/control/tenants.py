"""Tenants: who they are, where they run, and the token the control plane reads them with.

<dir>/tenants.json holds the tenants; <dir>/secrets.env (readable by its owner only) holds each tenant's service
token as TENANT_<ID>_TOKEN. A token may also come from the environment under the same name.
"""
from __future__ import annotations

import json
import os
import re
import secrets as pysecrets
import shutil
import socket
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SERVICE_USER = "controlplane"


class TenantError(ValueError):
    """Shown to the user as is."""


@dataclass
class Tenant:
    id: str
    name: str
    url: str  # how the control plane (and people) reach it, e.g. http://127.0.0.1:8101
    owner: str = ""
    description: str = ""
    managed: bool = False  # the control plane starts and restarts it
    workspace: str = ""  # managed: its workspace folder
    port: int = 0  # managed: the port it listens on
    running: bool = True  # managed: whether it should be running

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def token_key(self) -> str:
        return f"TENANT_{self.id.upper().replace('-', '_')}_TOKEN"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "tenant"


def check_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/") or parts.query:
        raise TenantError("The address is the instance's base URL, like https://autoplay.team-a.example.com.")
    return url


class TenantStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / "tenants.json"
        self.secrets_path = self.folder / "secrets.env"
        self._lock = threading.RLock()

    # ───── tenants ─────

    def list(self) -> list[Tenant]:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8")).get("tenants", [])
        except (OSError, ValueError, AttributeError):
            return []
        out = []
        for item in items if isinstance(items, list) else []:
            try:
                out.append(Tenant(**{k: item[k] for k in Tenant.__dataclass_fields__ if k in item}))
            except (TypeError, KeyError):
                continue
        return sorted(out, key=lambda t: t.name.casefold())

    def get(self, tid: str) -> Tenant | None:
        return next((t for t in self.list() if t.id == tid), None)

    def _write(self, tenants: list[Tenant]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name("tenants.json.tmp")
        tmp.write_text(json.dumps({"tenants": [t.to_dict() for t in tenants]}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, tenant: Tenant) -> Tenant:
        with self._lock:
            tenants = self.list()
            if not tenant.name.strip() or len(tenant.name) > 80:
                raise TenantError("Name the tenant (at most 80 characters).")
            if not ID_RE.match(tenant.id):
                raise TenantError("Invalid tenant id.")
            if any(t.id == tenant.id for t in tenants):
                raise TenantError(f"There already is a tenant {tenant.id!r}.")
            tenant.url = check_url(tenant.url)
            if any(t.url == tenant.url for t in tenants):
                raise TenantError(f"Another tenant already uses {tenant.url}.")
            self._write(tenants + [tenant])
            return tenant

    def update(self, tid: str, **changes) -> Tenant:
        with self._lock:
            tenants = self.list()
            tenant = next((t for t in tenants if t.id == tid), None)
            if tenant is None:
                raise TenantError("No such tenant.")
            for key, value in changes.items():
                if key in ("name", "owner", "description", "running"):
                    setattr(tenant, key, value)
            self._write(tenants)
            return tenant

    def remove(self, tid: str) -> Tenant | None:
        """Forget a tenant (its workspace and runs stay on disk)."""
        with self._lock:
            tenants = self.list()
            gone = next((t for t in tenants if t.id == tid), None)
            if gone:
                self._write([t for t in tenants if t.id != tid])
                self.set_token(gone, None)
            return gone

    def next_port(self, first: int) -> int:
        """The first port from `first` that no tenant has and nothing on this machine listens on or holds (on
        Windows, outgoing connections take ports one after another, so the next number is often taken)."""
        used = {t.port for t in self.list()}
        port = first
        while port in used or not port_free(port):
            port += 1
            if port > 65535:
                raise TenantError(f"No free port from {first}.")
        return port

    # ───── tokens ─────

    def token(self, tenant: Tenant) -> str:
        if os.environ.get(tenant.token_key):
            return os.environ[tenant.token_key]
        if self.secrets_path.is_file():
            return dotenv_values(self.secrets_path).get(tenant.token_key) or ""
        return ""

    def set_token(self, tenant: Tenant, token: str | None) -> None:
        if token is not None and not re.fullmatch(r"[\w.~+/=-]{8,200}", token):
            raise TenantError("That token does not look like an AutoPlay API token.")
        with self._lock:
            values = dict(dotenv_values(self.secrets_path)) if self.secrets_path.is_file() else {}
            if token is None:
                values.pop(tenant.token_key, None)
            else:
                values[tenant.token_key] = token
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.secrets_path.with_name("secrets.env.tmp")
            tmp.write_text("".join(f"{k}={v}\n" for k, v in values.items() if v), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(self.secrets_path)


# ───────────── a new tenant ─────────────

SKIP = shutil.ignore_patterns("reports", ".history", ".env", "locatorProfiles", "heals.json", "quarantine.json",
                              "monitors.json", "schedules.json", "__pycache__", ".git")


def create_workspace(folder: Path, template: Path | None = None) -> None:
    """A tenant's own workspace: a copy of `template` without its runs, users, secrets and app state, or an empty
    one. It always gets its own (empty) .env, so it never falls back to anybody else's logins."""
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise TenantError(f"{folder} already exists and is not empty.")
    if template is not None:
        template = Path(template)
        if not (template / "Data").is_dir():
            raise TenantError(f"{template} is not an AutoPlay workspace (no Data folder).")
        shutil.copytree(template, folder, ignore=SKIP, dirs_exist_ok=True)
    else:
        for sub in ("Data", "Pages", "Tests/com"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / "Data" / "systemData.json").write_text("{}\n", encoding="utf-8")
        (folder / "Tests" / "com" / "commonActions.json").write_text('{\n  "commonActions": {}\n}\n',
                                                                     encoding="utf-8")
    (folder / ".env").write_text("", encoding="utf-8")


def create_accounts(folder: Path, admin: str, password: str = "") -> tuple[str, str]:
    """In the tenant's own users file: its first admin, and a read-only service account for the control plane.
    Returns (the admin's password, generated when not given; the service token)."""
    from webapp.services.users import UserStore
    users = UserStore(Path(folder) / "reports" / "users.json")
    password = password or pysecrets.token_urlsafe(12)
    try:
        users.add(admin, password, "admin")
        users.add(SERVICE_USER, pysecrets.token_urlsafe(24), "viewer")
    except ValueError as e:
        raise TenantError(str(e)) from e
    return password, users.new_token(SERVICE_USER, "control plane")
