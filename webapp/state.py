"""Process-wide service instances (one RunManager per app)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import dotenv_values

from webapp.services.ai_assist import AIAssist
from webapp.services.audit import AuditLog
from webapp.services.common_actions import CommonActionService
from webapp.services.data_sets import DataSetService
from webapp.services.env_config import EnvConfigService
from webapp.services.help_service import HelpService
from webapp.services.notifier import Notifier
from webapp.services.pom_service import PomService
from webapp.services.recorder import Recorder
from webapp.services.run_artifacts import RunArtifacts
from webapp.services.run_manager import RunManager
from webapp.services.issue_tracker import IssueTracker
from webapp.services.journeys import JourneyService, JourneyStore
from webapp.services.releases import ReleaseService, ReleaseStore
from webapp.services.heals import HealService
from webapp.services.quarantine_service import QuarantineService
from webapp.services.runners import RunnerStore
from webapp.services.vault import Vault, missing_credentials
from webapp.services.schedules import Scheduler, ScheduleStore
from webapp.services.system_data import SystemDataService
from webapp.services.test_case_service import TestCaseService
from webapp.services.test_catalog import TestCatalogService
from webapp.services.users import UserStore
from webapp.settings import Settings, get_settings


@dataclass
class Services:
    settings: Settings
    env: EnvConfigService
    systems: SystemDataService
    catalog: TestCatalogService
    runs: RunManager
    runners: RunnerStore
    quarantine: QuarantineService
    common: CommonActionService
    journeys: JourneyService
    releases: ReleaseService
    heals: HealService
    issues: IssueTracker
    vault: Vault
    pom: PomService
    tests: TestCaseService
    help: HelpService
    artifacts: RunArtifacts
    recorder: Recorder
    schedules: ScheduleStore
    scheduler: Scheduler
    ai: AIAssist
    users: UserStore
    audit: AuditLog
    datasets: DataSetService


    def flag(self, key: str) -> bool:
        return env_flag(self.env, key)

    def recorder_enabled(self) -> bool:
        return self.flag("recorderEnabled")

    def recorder_blocker(self) -> str:
        """Why the recorder can't start right now ("" = it can)."""
        if not self.recorder_enabled():
            return "The test recorder is turned off. Turn it on with the Recorder switch in the Generator."
        if self.settings.host not in ("127.0.0.1", "localhost", "::1"):
            return ("The recorder opens a browser on the machine running the app, so it only works when the app "
                    "listens on this machine only (AUTOPLAY_HOST=127.0.0.1).")
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

    def start_journey(self, journey_id: str, env: str, browsers: list[str] | None = None):
        """Run every test case of a journey on one environment, with the saved browser options from .env."""
        from webapp.services.env_config import FIELDS
        from webapp.services.journeys import JourneyError
        from webapp.services.run_manager import RunConfig

        journey = self.journeys.store.get(journey_id)
        if journey is None:
            raise JourneyError("That journey no longer exists.")
        if env not in self.systems.environments(journey.system):
            raise JourneyError(f"{journey.system} has no environment {env!r} in systemData.json.")
        tags, test_ids = self.journeys.run_selection(journey)
        defaults = self.env.load()
        options = {f.key: defaults[f.key] for f in FIELDS if f.group in ("Browser", "Artifacts")
                   and f.key != "del_allure" and f.key in defaults}
        browsers = browsers or [b.strip() for b in defaults.get("browsers", "").split(",") if b.strip()] \
            or ["chromium"]
        credentials, _, _ = self.prepare_credentials(journey.system, [env])
        return self.runs.start(RunConfig(system=journey.system, env=env, tags=tags, browsers=browsers,
                                         options=options, test_ids=test_ids),
                               expected=len(test_ids) * len(browsers), trigger=f"journey:{journey.id}",
                               credentials=credentials)

    def prepare_credentials(self, system: str | None, envs: list[str | None], typed: dict[str, str] | None = None,
                            use_vault: bool = True) -> tuple[dict[str, str], list[str], dict[str, str]]:
        """Login values for a run on these environments, beyond what the environment and .env already have:
        (values, still missing, vault errors). Values typed in the app come first, then the secret store (when one
        is set up). Nothing is saved; the values belong to one run."""
        typed = typed or {}
        status: list[dict] = []
        for env in envs:
            status += [c for c in self.credential_status(system, env) or [] if c["key"] not in
                       {x["key"] for x in status}]
        wanted = [c["key"] for c in status if not c["set"]]
        values = {k: typed[k] for k in wanted if typed.get(k)}
        errors: dict[str, str] = {}
        if use_vault and self.vault.configured():
            missing = missing_credentials(status, values)
            if missing:
                found = self.vault.fetch(missing)
                values.update(found.values)
                errors = found.errors
        return values, [k for k in wanted if k not in values], errors


def env_flag(env: EnvConfigService, key: str) -> bool:
    return env.load().get(key, "false").strip().lower() in ("1", "true", "yes", "on")


def prefixed_lookup(env: EnvConfigService, prefix: str):
    """Values of <prefix>* keys from the environment or .env, for the services that need them (never shown)."""
    def lookup(key: str) -> str | None:
        if not key.startswith(prefix):
            return None
        if os.environ.get(key):
            return os.environ[key]
        return dotenv_values(env.env_path).get(key) if env.env_path.is_file() else None
    return lookup


def notify_lookup(env: EnvConfigService):
    """NOTIFY_* values from the environment or .env, for delivering notifications only (never shown)."""
    def lookup(key: str) -> str | None:
        if not key.startswith("NOTIFY_"):
            return None
        if os.environ.get(key):
            return os.environ[key]
        return dotenv_values(env.env_path).get(key) if env.env_path.is_file() else None
    return lookup


@lru_cache
def services() -> Services:
    s = get_settings()
    pom = PomService(s.workspace, s.repo_root)
    catalog = TestCatalogService(s.workspace)
    runners = RunnerStore(s.resolved_runs_dir.parent / "runners.json")
    runs = RunManager(
        repo_root=s.repo_root, workspace=s.workspace, runs_dir=s.resolved_runs_dir,
        python_exe=s.python_exe, allure_cmd=s.allure_cmd, max_concurrent=s.max_concurrent_runs,
        runners=runners,
    )
    env = EnvConfigService(s.dotenv_path)
    schedules = ScheduleStore(s.workspace / "Data" / "schedules.json")
    notifier = Notifier(notify_lookup(env), s.resolved_public_url)
    tests_service = TestCaseService(s.workspace, pom)
    artifacts = RunArtifacts("/runs", mask=runs.masker.mask)
    systems = SystemDataService(s.systemdata_path)
    common = CommonActionService(s.workspace, tests_service)
    quarantine = QuarantineService(s.workspace)
    journeys = JourneyService(JourneyStore(s.workspace), tests_service, common, catalog, systems, runs,
                              lambda run: artifacts.details(run.id, run.results_dir))
    svc = Services(
        pom=pom,
        tests=tests_service,
        help=HelpService(s.repo_root / "docs" / "help"),
        artifacts=artifacts,
        recorder=Recorder(s.python_exe, s.recorder_command or None),
        settings=s,
        env=env,
        systems=systems,
        catalog=catalog,
        runs=runs,
        runners=runners,
        quarantine=quarantine,
        schedules=schedules,
        scheduler=Scheduler(schedules, runs, catalog, notifier),
        ai=AIAssist(lambda: env_flag(env, "aiAssistEnabled"), env.env_path,
                    s.resolved_runs_dir.parent / "ai-audit.jsonl", s.ai_model),
        users=UserStore(s.resolved_users_file),
        audit=AuditLog(s.resolved_runs_dir.parent / "audit.jsonl"),
        datasets=DataSetService(s.workspace, tests_service),
        common=common,
        journeys=journeys,
        heals=HealService(s.workspace, runs, pom),
        releases=ReleaseService(ReleaseStore(s.resolved_runs_dir.parent / "releases"), journeys, runs, quarantine,
                                s.resolved_public_url),
        issues=IssueTracker(prefixed_lookup(env, "ISSUE_")),
        vault=Vault(prefixed_lookup(env, "SECRET_")),
    )
    svc.scheduler.credentials_for = lambda system, env, vault: svc.prepare_credentials(
        system, [env], use_vault=vault)[0]
    return svc
