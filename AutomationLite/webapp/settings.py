"""App settings. Override any field with an AUTOPLAY_<FIELD> env var (e.g. AUTOPLAY_PORT=9000)."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils.workspace import REPO_ROOT, resolve_dotenv_path, resolve_systemdata_path, resolve_workspace_dir


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOPLAY_", extra="ignore")

    workspace: Path = Field(default_factory=lambda: Path(resolve_workspace_dir()))
    runs_dir: Path | None = None  # default: <workspace>/reports/runs
    host: str = "127.0.0.1"
    # Extra host names the app may be reached by (comma-separated), e.g. a DNS name when host is not loopback
    allowed_hosts: str = ""
    port: int = 8080
    python_exe: str = sys.executable
    allure_cmd: str = "allure"  # Allure CLI; report generation is skipped if not found
    max_concurrent_runs: int = 1  # local runs drive real browsers; keep to one by default
    # Login with users and roles (python -m webapp.users). Always on when host is not loopback, unless
    # insecure_no_auth=true is set on purpose (e.g. behind a reverse proxy that does its own login).
    auth_enabled: bool = False
    insecure_no_auth: bool = False
    users_file: Path | None = None  # default: <workspace>/reports/users.json
    # Single sign-on with OpenID Connect (webapp/services/oidc.py); on when issuer and client id are set
    oidc_issuer: str = ""  # e.g. https://login.microsoftonline.com/<tenant>/v2.0
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_username_claim: str = "email"  # the claim that names the person (must be one they cannot change)
    oidc_groups_claim: str = "groups"
    oidc_role_map: str = ""  # "group=role, group=SYSTEM/env:role, ..."
    oidc_default_role: str = ""  # for people in no mapped group who have no account yet ("": refused)
    oidc_label: str = "single sign-on"  # the sign-in button says "Sign in with <label>"
    oidc_only: bool = False  # no password sign-in for people (API tokens still work)
    # argv that runs the Playwright CLI for the recorder, as a JSON list (default: <python_exe> -m playwright)
    recorder_command: list[str] = Field(default_factory=list)
    # How people reach this app, for links in notifications (default http://127.0.0.1:<port>)
    public_url: str = ""
    ai_model: str = "claude-opus-5-5"  # AI assist model (when aiAssistEnabled=true in .env)
    scheduler_enabled: bool = True  # fire schedules from this process (off for extra app instances)
    exit_with_parent: bool = False  # set by the control plane: stop when its stdin closes (the parent is gone)

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def dotenv_path(self) -> Path:
        return Path(resolve_dotenv_path(str(self.workspace)))

    @property
    def systemdata_path(self) -> Path:
        return Path(resolve_systemdata_path(str(self.workspace)))

    @property
    def resolved_users_file(self) -> Path:
        return self.users_file or self.resolved_runs_dir.parent / "users.json"

    @property
    def login_required(self) -> bool:
        return self.auth_enabled or (self.host not in ("127.0.0.1", "localhost", "::1") and not self.insecure_no_auth)

    @property
    def sso_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)

    @property
    def resolved_public_url(self) -> str:
        if self.public_url:
            return self.public_url.rstrip("/")
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}"

    @property
    def resolved_runs_dir(self) -> Path:
        return self.runs_dir or self.workspace / "reports" / "runs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
