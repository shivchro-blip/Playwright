"""App settings. Override any field with an AUTOMATENY_<FIELD> env var (e.g. AUTOMATENY_PORT=9000)."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils.workspace import REPO_ROOT, resolve_dotenv_path, resolve_systemdata_path, resolve_workspace_dir


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATENY_", extra="ignore")

    workspace: Path = Field(default_factory=lambda: Path(resolve_workspace_dir()))
    runs_dir: Path | None = None  # default: <workspace>/reports/runs
    host: str = "127.0.0.1"
    # Extra host names the app may be reached by (comma-separated), e.g. a DNS name when host is not loopback
    allowed_hosts: str = ""
    port: int = 8080
    python_exe: str = sys.executable
    allure_cmd: str = "allure"  # Allure CLI; report generation is skipped if not found
    max_concurrent_runs: int = 1  # local runs drive real browsers; keep to one by default
    auth_enabled: bool = False  # reserved for the shared-server phase
    # argv that runs the Playwright CLI for the recorder, as a JSON list (default: <python_exe> -m playwright)
    recorder_command: list[str] = Field(default_factory=list)

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
    def resolved_runs_dir(self) -> Path:
        return self.runs_dir or self.workspace / "reports" / "runs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
