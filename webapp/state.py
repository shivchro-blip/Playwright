"""Process-wide service instances (one RunManager per app)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from webapp.services.env_config import EnvConfigService
from webapp.services.help_service import HelpService
from webapp.services.pom_service import PomService
from webapp.services.recorder import Recorder
from webapp.services.run_artifacts import RunArtifacts
from webapp.services.run_manager import RunManager
from webapp.services.system_data import SystemDataService
from webapp.services.test_case_service import TestCaseService
from webapp.services.test_catalog import TestCatalogService
from webapp.settings import Settings, get_settings


@dataclass
class Services:
    settings: Settings
    env: EnvConfigService
    systems: SystemDataService
    catalog: TestCatalogService
    runs: RunManager
    pom: PomService
    tests: TestCaseService
    help: HelpService
    artifacts: RunArtifacts
    recorder: Recorder

    def recorder_enabled(self) -> bool:
        return self.env.load().get("recorderEnabled", "false").strip().lower() in ("1", "true", "yes", "on")

    def recorder_blocker(self) -> str:
        """Why the recorder can't start right now ("" = it can)."""
        if not self.recorder_enabled():
            return "The test recorder is turned off. Turn it on with the Recorder switch in the Generator."
        if self.settings.host not in ("127.0.0.1", "localhost", "::1"):
            return ("The recorder opens a browser on the machine running the app, so it only works when the app "
                    "listens on this machine only (AUTOMATENY_HOST=127.0.0.1).")
        return ""

    def credential_status(self, system: str | None, env: str | None) -> list[dict] | None:
        """Which credential env vars a system/env needs and whether each is set. Never returns values.
        None = no systemData entry; [] = entry needs no credentials (e.g. public site)."""
        login = self.systems.login(system, env)
        if not login:
            return None
        status = []
        for key in (login.username_env, login.password_env):
            if key:
                status.append({"key": key, "set": bool(os.environ.get(key)) or self.env.is_set(key)})
        return status


@lru_cache
def services() -> Services:
    s = get_settings()
    pom = PomService(s.workspace, s.repo_root)
    return Services(
        pom=pom,
        tests=TestCaseService(s.workspace, pom),
        help=HelpService(s.repo_root / "docs" / "help"),
        artifacts=RunArtifacts("/runs"),
        recorder=Recorder(s.python_exe, s.recorder_command or None),
        settings=s,
        env=EnvConfigService(s.dotenv_path),
        systems=SystemDataService(s.systemdata_path),
        catalog=TestCatalogService(s.workspace),
        runs=RunManager(
            repo_root=s.repo_root, workspace=s.workspace, runs_dir=s.resolved_runs_dir,
            python_exe=s.python_exe, allure_cmd=s.allure_cmd, max_concurrent=s.max_concurrent_runs,
        ),
    )
