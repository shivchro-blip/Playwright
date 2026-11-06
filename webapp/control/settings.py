"""Control plane settings: AUTOPLAY_CP_<FIELD> env vars (e.g. AUTOPLAY_CP_PORT=9100)."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils.workspace import REPO_ROOT


class ControlSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOPLAY_CP_", extra="ignore")

    dir: Path = Field(default_factory=lambda: REPO_ROOT / "controlplane")  # tenants.json, secrets, logs, users
    host: str = "127.0.0.1"
    port: int = 8090
    auth_enabled: bool = False
    insecure_no_auth: bool = False
    python_exe: str = sys.executable
    first_port: int = 8101  # managed tenants get the next free port from here
    supervise: bool = True  # start the managed tenants with the control plane and restart them if they stop

    @property
    def login_required(self) -> bool:
        return self.auth_enabled or (self.host not in ("127.0.0.1", "localhost", "::1") and not self.insecure_no_auth)


@lru_cache
def get_control_settings() -> ControlSettings:
    return ControlSettings()
