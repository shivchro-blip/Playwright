"""Process-wide service instances (one RunManager per app)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from webapp.services.env_config import EnvConfigService
from webapp.services.help_service import HelpService
from webapp.services.pom_service import PomService
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
        settings=s,
        env=EnvConfigService(s.dotenv_path),
        systems=SystemDataService(s.systemdata_path),
        catalog=TestCatalogService(s.workspace),
        runs=RunManager(
            repo_root=s.repo_root, workspace=s.workspace, runs_dir=s.resolved_runs_dir,
            python_exe=s.python_exe, allure_cmd=s.allure_cmd, max_concurrent=s.max_concurrent_runs,
        ),
    )
