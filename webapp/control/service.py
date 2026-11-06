"""The control plane's services: tenants, their processes, the portfolio, the control plane's own users."""
from __future__ import annotations

import logging
import shutil
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from webapp.control.portfolio import Portfolio
from webapp.control.settings import ControlSettings, get_control_settings
from webapp.control.supervisor import Supervisor
from webapp.control.tenants import (
    Tenant, TenantError, TenantStore, check_url, create_accounts, create_workspace, port_free, slug,
)
from webapp.services.audit import AuditLog
from webapp.services.users import UserStore

logger = logging.getLogger(__name__)


@dataclass
class ControlPlane:
    settings: ControlSettings
    tenants: TenantStore
    supervisor: Supervisor
    portfolio: Portfolio
    users: UserStore
    audit: AuditLog

    def create(self, name: str, admin: str, *, owner: str = "", description: str = "", password: str = "",
               template: str = "", workspace: str = "", port: int = 0) -> tuple[Tenant, str]:
        """A managed tenant: its own workspace, admin and service account, on its own port. Returns the tenant
        and the admin's password (shown once)."""
        tid = slug(name)
        if self.tenants.get(tid):
            raise TenantError(f"There already is a tenant {tid!r}; choose another name.")
        if not re.fullmatch(r"[\w.@-]{1,64}", admin or ""):
            raise TenantError("Name the tenant's first admin (letters, digits and . _ @ -).")
        port = port or self.tenants.next_port(self.settings.first_port)
        if any(t.port == port for t in self.tenants.list()) or not 1024 <= port <= 65535 or not port_free(port):
            raise TenantError(f"Port {port} is taken or not allowed.")
        folder = Path(workspace) if workspace else self.settings.dir / "tenants" / tid
        existed = folder.exists()
        create_workspace(folder, Path(template) if template else None)
        try:
            password, token = create_accounts(folder, admin, password)
        except TenantError:
            if not existed:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        tenant = Tenant(tid, name.strip(), f"http://127.0.0.1:{port}", owner.strip(), description.strip(),
                        managed=True, workspace=str(folder.resolve()), port=port)
        self.tenants.add(tenant)
        self.tenants.set_token(tenant, token)
        self.portfolio.forget()
        return tenant, password

    def register(self, name: str, url: str, token: str, *, owner: str = "", description: str = "") -> Tenant:
        """An instance that runs elsewhere (the control plane only reads it)."""
        tenant = Tenant(slug(name), name.strip(), check_url(url), owner.strip(), description.strip())
        if not token:
            raise TenantError("Give the instance's API token (python -m webapp.users token NAME there, for a "
                              "viewer account).")
        self.tenants.add(tenant)
        self.tenants.set_token(tenant, token)
        self.portfolio.forget()
        return tenant

    def remove(self, tid: str) -> Tenant | None:
        self.supervisor.stop(tid)
        gone = self.tenants.remove(tid)
        self.portfolio.forget()
        return gone

    def set_running(self, tid: str, running: bool) -> Tenant:
        tenant = self.tenants.get(tid)
        if tenant is None:
            raise TenantError("No such tenant.")
        if not tenant.managed:
            raise TenantError("The control plane does not run this tenant; it only reads it.")
        tenant = self.tenants.update(tid, running=running)
        if running:
            self.supervisor.start(tenant)
        else:
            self.supervisor.stop(tid)
        self.portfolio.forget()
        return tenant

    def restart(self, tid: str) -> None:
        tenant = self.set_running(tid, True)
        self.supervisor.stop(tid)
        self.supervisor.start(tenant)


@lru_cache
def control() -> ControlPlane:
    s = get_control_settings()
    store = TenantStore(s.dir)
    return ControlPlane(s, store, Supervisor(store, s.python_exe, s.dir / "logs"), Portfolio(store),
                        UserStore(s.dir / "users.json"), AuditLog(s.dir / "audit.jsonl"))
