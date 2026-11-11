"""Runs the framework as background jobs: one pytest subprocess per run, own folder per run.

runs/<id>/
    run.json          status, config, per-test results (history)
    console.log       full console output
    allure-results/   raw allure-pytest output
    allure-report/    generated HTML report (if the Allure CLI is available)
    report.html       built-in report: summary, failures, recent results per test, trend (no Allure needed)
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import re
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from webapp.pytest_events import PREFIX
from webapp.services.archives import extract_safely
from webapp.services.cleanup import looks_like_run_folder, remove_tree, tree_size
from webapp.services.env_config import LEVELS, RUN_OPTION_KEYS, format_value, is_secret
from webapp.services.events import EventBus
from webapp.services.json_files import NAME_RE
from webapp.services.masking import Masker
from webapp.services.results_index import ResultsIndex
from webapp.services.run_artifacts import RunArtifacts
from webapp.services.run_insights import build_report
from webapp.services.runners import RunnerStore

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 20_000
MAX_QUEUED = 20  # runs waiting for a free slot
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SUITE = "tests/dynamic_test_suite.py"
ACTIVE = ("queued", "running")
INDEX_SUFFIX = ".results.db"  # the results index: <runs folder name>.results.db beside the runs folder


def index_path(runs_dir: Path) -> Path:
    """Where the results index of a runs folder lives (webapp/services/results_index.py): next to the folder,
    not in it, so the folder holds only run folders."""
    runs_dir = Path(runs_dir)
    return runs_dir.parent / f"{runs_dir.name}{INDEX_SUFFIX}"
RUNNER_SILENCE_SECONDS = 90  # a remote run whose runner has not called in this long is marked as lost
WATCH_INTERVAL = 15  # seconds between checks for lost runners


def is_truthy(value: object) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class RunConfig:
    system: str
    env: str
    tags: list[str]
    browsers: list[str] = field(default_factory=lambda: ["chrome"])
    workers: int = 1
    # Extra framework settings for this run only (e.g. headlessMode, slowmo, recording)
    options: dict[str, object] = field(default_factory=dict)
    # Optional: run only these testCaseIDs (still filtered by tags first)
    test_ids: list[str] = field(default_factory=list)
    # "" = this machine; otherwise a registered remote runner's name, or "label:<x>" for any free runner with that
    # label (webapp/services/runners.py)
    runner: str = ""


STEP_POLL_SECONDS = 0.5  # how often a running run's step events are read
LOG_PUBLISH_SECONDS = 0.5  # at most one "log" event per run this often


@dataclass
class Purged:
    """What RunManager.purge_finished deleted: run ids, leftover folders, bytes, and what could not be removed."""
    runs: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    bytes_freed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TestResult:
    nodeid: str
    test_id: str
    browser: str
    outcome: str  # running | passed | failed | skipped | error | xfailed | xpassed | cancelled
    duration: float = 0.0
    message: str = ""
    retries: int = 0  # times a failed attempt was run again (retryFailed); passed with retries > 0 = flaky
    healed: int = 0  # locators that found nothing and were healed during the run (selfHeal)
    # the test's own steps as they ran: {n, of, kind, label, status (running|passed|failed), seconds, error}
    steps: list[dict] = field(default_factory=list)
    step: str = ""  # while running: the step it is on, e.g. "3 of 5: Payment"


@dataclass
class Run:
    id: str
    config: RunConfig
    dir: Path
    expected: int = 0
    status: str = "queued"  # queued | running | passed | failed | error | cancelled
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    error: str = ""
    collected: int | None = None
    report_status: str = ""  # "" | generating | ready | unavailable | failed
    report_message: str = ""
    trigger: str = ""  # "" = started by a person; "schedule:<id>" = started by a schedule
    claimed_by: str = ""  # remote runs: the runner that took it
    compare_with: str = ""  # a run started together with this one on another environment, to compare with
    debug_pauses: int = 0  # times the run waited for a person after a failure (debugOnFailure): not a clean result
    results: dict[str, TestResult] = field(default_factory=dict)
    log: list[str] = field(default_factory=list, repr=False)
    log_offset: int = 0  # lines dropped from the front of `log` (memory cap)
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _cancelled: bool = field(default=False, repr=False)
    _last_seen: float = field(default=0.0, repr=False)  # remote runs: time.monotonic() of the runner's last call
    _finishing: bool = field(default=False, repr=False)
    # Login values typed into the app for this run: memory only, never saved, logged or serialized
    _secrets: dict[str, str] = field(default_factory=dict, repr=False)
    _masker: Masker | None = field(default=None, repr=False)  # hides SSNs, card numbers and the like
    _step_offsets: dict = field(default_factory=dict, repr=False)  # step event file -> bytes already read
    _steps_read_at: float = field(default=0.0, repr=False)
    _log_published: float = field(default=0.0, repr=False)
    # While the test waits after a failure (utils/debug_pause.py): what failed and where; None otherwise. Memory only.
    paused: dict | None = field(default=None, repr=False)

    def scrub(self, text: str) -> str:
        for value in self._secrets.values():
            if len(value) >= 3:
                text = text.replace(value, "***")
        return self._masker.mask(text) if self._masker else text

    @property
    def results_dir(self) -> Path:
        return self.dir / "allure-results"

    @property
    def report_dir(self) -> Path:
        return self.dir / "allure-report"

    @property
    def summary_file(self) -> Path:
        return self.dir / "report.html"

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in list(self.results.values()):
            counts[r.outcome] = counts.get(r.outcome, 0) + 1
        return counts

    def log_since(self, line_no: int) -> tuple[list[str], int]:
        """Lines after absolute line number `line_no`, and the new absolute end."""
        start = max(line_no - self.log_offset, 0)
        lines = self.log[start:]
        return lines, self.log_offset + start + len(lines)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "config": asdict(self.config),
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "error": self.error,
            "expected": self.expected,
            "collected": self.collected,
            "counts": self.counts(),
            "report_status": self.report_status,
            "report_message": self.report_message,
            "trigger": self.trigger,
            "claimed_by": self.claimed_by,
            "compare_with": self.compare_with,
            "debug_pauses": self.debug_pauses,
            "results": [asdict(r) for r in list(self.results.values())],
        }



FINISHED = ("passed", "failed", "error", "cancelled")


def filter_runs(runs, system: str | None = None, env: str | None = None, *, since: str | None = None,
                until: str | None = None, finished_only: bool = False, trigger: str | None = None,
                trigger_prefix: str | None = None, limit: int | None = None) -> list[Run]:
    """RunManager.query in memory: the reference the results index answers to (and its fallback)."""
    out = sorted((r for r in runs
                  if (system is None or r.config.system == system) and (env is None or r.config.env == env)
                  and (not since or r.created_at >= since) and (not until or r.created_at < until)
                  and (not finished_only or r.status in FINISHED)
                  and (trigger is None or r.trigger == trigger)
                  and (not trigger_prefix or (r.trigger or "").startswith(trigger_prefix))),
                 key=lambda r: (r.created_at, r.id), reverse=True)
    return out[:limit] if limit else out


def query_runs(manager, system: str | None = None, env: str | None = None, **filters) -> list[Run]:
    """manager.query(...) when it has one (RunManager), else the same filter over manager.list()."""
    if hasattr(manager, "query"):
        return manager.query(system, env, **filters)
    return filter_runs(manager.list(), system, env, **filters)


def run_from_dict(data: dict, folder: Path) -> Run:
    """A run as saved in run.json (or the results index) back into a Run. Raises KeyError/TypeError if malformed."""
    return Run(
        id=data["id"], config=RunConfig(**data["config"]), dir=Path(folder),
        expected=data.get("expected", 0), status=data.get("status", "error"),
        created_at=data.get("created_at", ""), started_at=data.get("started_at", ""),
        finished_at=data.get("finished_at", ""), exit_code=data.get("exit_code"),
        error=data.get("error", ""), collected=data.get("collected"),
        report_status=data.get("report_status", ""), report_message=data.get("report_message", ""),
        trigger=data.get("trigger", ""), claimed_by=data.get("claimed_by", ""),
        compare_with=data.get("compare_with", ""), debug_pauses=int(data.get("debug_pauses") or 0),
        results={r["nodeid"]: TestResult(**r) for r in data.get("results", [])},
    )


def pytest_command(run: Run, python_exe: str, repo_root: Path) -> list[str]:
    """The pytest command line for a run (the web app and the runner agent build it the same way)."""
    cfg = run.config
    cmd = [
        python_exe, "-m", "pytest", SUITE,
        "-p", "webapp.pytest_events",
        # Ignore the local (gitignored) pytest.ini addopts - they need plugins not
        # every machine has - and set what the web app relies on explicitly.
        "-o", "addopts=",
        "-o", "log_cli=true",
        "-o", f"log_cli_level={cfg.options.get('LOG_LEVEL') or 'INFO'}",
        "-v",
        f"--rootdir={repo_root}",
        f"--alluredir={run.results_dir}",
        "--clean-alluredir",
    ]
    retries = int(cfg.options.get("retryFailed") or 0)
    if retries > 0:  # pytest-rerunfailures: run a failed test again, up to this many times
        cmd += ["--reruns", str(retries)]
    if cfg.workers > 1:
        cmd += ["-n", str(cfg.workers)]
    else:
        cmd.append("-s")  # stream framework prints live
    return cmd


def pytest_environment(run: Run, workspace: Path) -> dict[str, str]:
    """The environment of a run's pytest process."""
    cfg = run.config
    env = dict(os.environ)
    # The suite loads .env with override=False, so these per-run values win
    env.update({k: format_value(v) for k, v in cfg.options.items()})
    level = cfg.options.get("LOG_LEVEL")
    if level:  # one choice for every framework logger, ahead of any per-module level in .env
        env.update({k: str(level) for k in ("TestSuiteLoggerLevel", "PageLoggerLevel", "FormHandlerLoggerLevel")})
    env["checkpointsDir"] = str(run.dir / "checkpoints")  # used when saveCheckpoints is on
    if is_truthy(cfg.options.get("debugOnFailure")):  # only a run that asked for it can pause (utils/debug_pause.py)
        env["debugDir"] = str(run.dir / "debug")
    else:
        env.pop("debugDir", None)
        env["debugOnFailure"] = "false"  # whatever .env says
    for key, value in run._secrets.items():
        env.setdefault(key, value)  # never override what the environment already has
    env.update({
        "systemConfig": cfg.system,
        "envConfig": cfg.env,
        "runTags": ",".join(cfg.tags),
        "browsers": ",".join(cfg.browsers),
        "runTestIds": ",".join(cfg.test_ids),
        "del_allure": "false",  # each run has its own fresh results folder
        "reportsDir": str(run.results_dir),
        "AUTOPLAY_WORKSPACE": str(workspace),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


class RunManager:
    def __init__(self, repo_root: Path, workspace: Path, runs_dir: Path, python_exe: str,
                 allure_cmd: str = "allure", max_concurrent: int = 1, runners: RunnerStore | None = None,
                 bus: EventBus | None = None):
        self.repo_root = Path(repo_root)
        self.workspace = Path(workspace)
        self.runs_dir = Path(runs_dir)
        self.python_exe = python_exe
        self.allure_cmd = allure_cmd
        self.max_concurrent = max_concurrent
        self.runners = runners
        self.masker = Masker(self.workspace / "Data" / "maskPatterns.json")
        # Says what changed in a run (webapp/services/events.py): saved, result, step, finished, deleted
        self.bus = bus or EventBus()
        self._watchdog: threading.Thread | None = None
        self._save_lock = threading.Lock()  # run.json is written from the run's thread and from callers
        self._steps_lock = threading.Lock()  # step event files are read from the run thread and from pages
        self._runs: dict[str, Run] = {}
        self._queue: list[Run] = []  # FIFO: runs waiting for one of max_concurrent slots
        self._lock = threading.Lock()
        # Called with each run once it has finished and its reports are written (e.g. notifications)
        self.listeners: list[Callable[[Run], None]] = []
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.index = self._open_index()
        self._load_history()

    # ───────────── queries ─────────────

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def query(self, system: str | None = None, env: str | None = None, *, since: str | None = None,
              until: str | None = None, finished_only: bool = False, trigger: str | None = None,
              trigger_prefix: str | None = None, limit: int | None = None) -> list[Run]:
        """The runs matching a scope, newest first, picked through the results index (the live Run objects).
        Same answer as filtering list(): runs of `system`/`env`, created in [since, until), finished
        (passed/failed/error/cancelled) when finished_only, with exactly `trigger` or starting `trigger_prefix`."""
        filters = dict(since=since, until=until, finished_only=finished_only, trigger=trigger,
                       trigger_prefix=trigger_prefix, limit=limit)
        if self.index is not None:
            try:
                return [run for run in (self._runs.get(i) for i in self.index.ids(system, env, **filters)) if run]
            except Exception:  # noqa: BLE001 - a broken index must never stop the app answering
                logger.exception("Results index query failed; filtering in memory")
        return filter_runs(self.list(), system, env, **filters)

    def allure_available(self) -> bool:
        return shutil.which(self.allure_cmd) is not None

    # ───────────── commands ─────────────

    def start_compared(self, config: RunConfig, envs: list[str], expected: int = 0, trigger: str = "",
                       credentials: dict[str, str] | None = None) -> list[Run]:
        """Start `config` and the same selection on each environment in `envs`; the runs are paired so the
        comparison opens with its partner. Returns the main run first."""
        extra = [e for e in dict.fromkeys(str(e).strip() for e in envs) if e and e != config.env]
        first = self.start(config, expected, trigger, credentials)
        runs = [first]
        for env in extra:
            other = replace(config, env=env, tags=list(config.tags), browsers=list(config.browsers),
                            options=dict(config.options), test_ids=list(config.test_ids))
            run = self.start(other, expected, trigger, credentials, compare_with=first.id)
            if not first.compare_with:
                first.compare_with = run.id
                self._save(first)
            runs.append(run)
        return runs

    def start(self, config: RunConfig, expected: int = 0, trigger: str = "",
              credentials: dict[str, str] | None = None, compare_with: str = "") -> Run:
        """`credentials`: env var name -> value for this run only, held in memory. A variable the
        environment already has is left alone."""
        credentials = {k: v for k, v in (credentials or {}).items() if v}
        bad_keys = [k for k in credentials if not ENV_KEY_RE.match(k)]
        if bad_keys:
            raise ValueError(f"Invalid credential names: {', '.join(bad_keys)}")
        config.tags = [t.strip() for t in config.tags if t and t.strip()]
        config.browsers = [b.strip().lower() for b in config.browsers if b and b.strip()] or ["chrome"]
        config.workers = max(1, int(config.workers or 1))
        if not config.system:
            raise ValueError("Select a system.")
        if not NAME_RE.match(config.system) or config.system in (".", ".."):
            raise ValueError(f"Invalid system: {config.system!r}")
        config.runner = (config.runner or "").strip()
        if config.runner:
            if not self.runners or not self.runners.valid_target(config.runner):
                raise ValueError(f"Unknown runner: {config.runner!r}")
            local_only = [k for k in ("saveCheckpoints", "resumeCheckpoint") if config.options.get(k)]
            if local_only:
                raise ValueError(f"{', '.join(local_only)} only works on this machine, not on a remote runner.")
        if not config.tags:
            raise ValueError("Select at least one tag - the framework runs only tagged tests.")
        bad = [k for k in config.options if is_secret(k)]
        if bad:
            raise ValueError(f"Credentials cannot be passed as run options: {', '.join(bad)}")
        # Options become environment variables of the pytest process: only the typed framework settings
        unknown = sorted(k for k in config.options if k not in RUN_OPTION_KEYS)
        if unknown:
            raise ValueError(f"Unsupported run options: {', '.join(unknown)}")

        level = config.options.get("LOG_LEVEL")
        if level is not None and level not in LEVELS:
            raise ValueError(f"Log level must be one of {', '.join(LEVELS)}.")
        data_set = str(config.options.get("runDataSet") or "")
        if data_set and not re.fullmatch(r"[\w\-]+", data_set):
            raise ValueError("The data set name may only have letters, digits, '_' and '-'.")
        self._check_step_options(config)
        if is_truthy(config.options.get("debugOnFailure")):
            if trigger:
                raise ValueError("Pause on failure is only for runs you start yourself: a schedule, monitor or "
                                 "pipeline run would wait for nobody.")
            if config.runner:
                raise ValueError("Pause on failure works on this machine only, not on a remote runner.")
            if config.workers > 1:
                raise ValueError("Pause on failure runs one test at a time: set Parallel workers to 1.")

        with self._lock:
            if len(self._queue) >= MAX_QUEUED:
                raise RuntimeError(f"{MAX_QUEUED} runs are already waiting; try again when some have finished.")
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            run = Run(id=run_id, config=config, dir=self.runs_dir / run_id, expected=expected, trigger=trigger,
                      compare_with=compare_with)
            run.dir.mkdir(parents=True)
            run._secrets = credentials
            run._masker = self.masker
            self._runs[run_id] = run
            self._queue.append(run)
        self._save(run)
        if config.runner:
            self._ensure_watchdog()
        self._dispatch()
        return run

    def _check_step_options(self, config: RunConfig) -> None:
        """Running from a step is for one test case, with whole-number steps and a saved state or URL that is ours."""
        o = config.options
        steps = {}
        for key in ("runFromStep", "runToStep"):
            try:
                steps[key] = int(o.get(key) or 0)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a whole number.") from None
            if steps[key] < 0:
                raise ValueError(f"{key} must not be negative.")
        if steps["runFromStep"] and steps["runToStep"] and steps["runToStep"] < steps["runFromStep"]:
            raise ValueError("The last step comes before the first step.")
        url, resume = str(o.get("startUrl") or "").strip(), str(o.get("resumeCheckpoint") or "").strip()
        if url:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("The start URL must be a full http(s) URL.")
        if resume:
            path = Path(resume).resolve()
            ok = (path.suffix == ".json" and path.parent.parent.name == "checkpoints" and path.is_file()
                  and self.runs_dir.resolve() in path.parents)
            if not ok:
                raise ValueError("The saved state is not one of this app's checkpoints.")
        if (steps["runFromStep"] or steps["runToStep"] or url or resume) and len(set(config.test_ids)) != 1:
            raise ValueError("Running from a step needs exactly one test case.")

    def queue_position(self, run_id: str) -> int | None:
        """1 = next to start; None when the run is not waiting."""
        run = self._runs.get(run_id)
        with self._lock:
            ids = [r.id for r in self._queue if run and r.config.runner == run.config.runner]
        return ids.index(run_id) + 1 if run_id in ids else None

    def _dispatch(self) -> None:
        """Start queued runs while slots are free."""
        while True:
            with self._lock:
                running = sum(1 for r in self._runs.values() if r.status == "running" and not r.config.runner)
                run = next((r for r in self._queue if not r.config.runner), None)  # remote runs wait for their runner
                if running >= self.max_concurrent or run is None:
                    return
                self._queue.remove(run)
                run.status = "running"  # claim the slot before the thread starts
            threading.Thread(target=self._execute, args=(run,), name=f"run-{run.id}", daemon=True).start()

    def cancel(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if not run or not run.active:
            return False
        with self._lock:
            waiting = run in self._queue
            if waiting:
                self._queue.remove(run)
        if waiting:
            run.status, run.finished_at, run._cancelled = "cancelled", _now(), True
            self._append(run, "[webapp] Cancelled before it started.")
            self._save(run)
            return True
        run._cancelled = True
        proc = run._proc
        if proc and proc.poll() is None:
            self._kill_tree(proc)
        return True

    def debug_command(self, run_id: str, do: str) -> bool:
        """Tell a run that waits after a failure what to do: retry, restep, skip or abort (utils/debug_pause.py).
        False when the run is not waiting (it may have gone on, or ended, in the meantime)."""
        run = self._runs.get(run_id)
        paused = run.paused if run else None
        if not run or not run.active or not paused or do not in ("retry", "restep", "skip", "abort"):
            return False
        if do == "skip" and not paused.get("can_skip"):
            return False
        folder = run.dir / "debug"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "command.json.tmp"
        tmp.write_text(json.dumps({"do": do, "seq": paused.get("seq")}), encoding="utf-8")
        tmp.replace(folder / "command.json")
        return True

    def delete(self, run_id: str) -> bool:
        """Remove a finished run and its folder."""
        run = self._runs.get(run_id)
        if not run or run.active:
            return False
        folder = run.dir.resolve()
        if self.runs_dir.resolve() not in folder.parents:
            return False
        shutil.rmtree(folder, ignore_errors=True)
        self._forget(run)
        return True

    def _forget(self, run: Run) -> None:
        """Drop a deleted run from memory and the results index, and tell the pages."""
        with self._lock:
            self._runs.pop(run.id, None)
        self._index_call("remove", run.id)
        self.bus.publish("run", id=run.id, change="deleted", status=run.status, system=run.config.system,
                         env=run.config.env)

    def purge_finished(self) -> "Purged":
        """Delete every finished run's folder, and leftover run folders no run owns. Runs in progress or waiting
        are never touched. A run whose folder could not be removed completely stays listed (and is reported), so
        the history never claims something is gone that is still on disk."""
        with self._lock:  # start() creates a run's folder under this lock: no new run can look like a leftover
            finished = [r for r in self._runs.values() if not r.active]
            owned = set(self._runs)
        out = Purged()
        base = self.runs_dir.resolve()
        for run in finished:
            folder = run.dir.resolve()
            if base not in folder.parents:
                continue
            size = tree_size(folder)
            failures = remove_tree(folder)
            out.failures += failures
            if folder.exists():  # not completely gone: keep it listed
                out.bytes_freed += max(size - tree_size(folder), 0)
                continue
            out.bytes_freed += size
            out.runs.append(run.id)
            self._forget(run)
        for folder in sorted(self.runs_dir.iterdir()) if self.runs_dir.is_dir() else []:
            if folder.name in owned or not looks_like_run_folder(folder):
                continue
            size = tree_size(folder)
            out.failures += remove_tree(folder)
            if not folder.exists():
                out.orphans.append(folder.name)
                out.bytes_freed += size
        self._index_call("sync", self.runs_dir)  # drop index rows whose folders are gone
        return out

    def rerun(self, run_id: str) -> Run:
        old = self._runs.get(run_id)
        if not old:
            raise ValueError("Run not found.")
        cfg = old.config
        config = RunConfig(system=cfg.system, env=cfg.env, tags=list(cfg.tags), browsers=list(cfg.browsers),
                           workers=cfg.workers, options=dict(cfg.options), test_ids=list(cfg.test_ids))
        return self.start(config, expected=old.collected or old.expected, credentials=old._secrets)

    # ───────────── internals ─────────────

    def _command(self, run: Run) -> list[str]:
        return pytest_command(run, self.python_exe, self.repo_root)

    def _environment(self, run: Run) -> dict[str, str]:
        return pytest_environment(run, self.workspace)

    def _execute(self, run: Run) -> None:
        run.status = "running"
        run.started_at = _now()
        self._save(run)
        cmd = self._command(run)
        self._append(run, f"[webapp] $ {' '.join(cmd)}")
        self._append(run, f"[webapp] system={run.config.system} env={run.config.env} "
                          f"tags={','.join(run.config.tags)} browsers={','.join(run.config.browsers)}")

        console = (run.dir / "console.log").open("w", encoding="utf-8")
        try:
            popen_kwargs = {}
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True
            run._proc = subprocess.Popen(
                cmd, cwd=self.repo_root, env=self._environment(run),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1, **popen_kwargs,
            )
            if run._cancelled:  # cancel arrived while spawning
                self._kill_tree(run._proc)
            self._follow_steps(run)
            for raw in run._proc.stdout:
                self._ingest_line(run, console, raw)
            run.exit_code = run._proc.wait()
        except Exception as e:  # spawn failure etc.
            logger.exception("Run %s failed", run.id)
            run.error = str(e)
            self._append(run, f"[webapp] ERROR: {e}")
        finally:
            console.close()
            run._proc = None
        self._finish(run)

    def _ingest_line(self, run: Run, console, raw: str) -> None:
        """One line of pytest output: a framework event, or console text for the log and console.log."""
        if time.monotonic() - run._steps_read_at > STEP_POLL_SECONDS:
            self.read_steps(run)
        line = raw.rstrip("\r\n")
        # `-v` prints the node id before the outcome, so an event can start mid-line
        idx = line.find(PREFIX)
        if idx >= 0:
            self._handle_event(run, line[idx + len(PREFIX):])
            line = line[:idx]
            if not line.strip():
                return
        line = run.scrub(line)
        console.write(line + "\n")
        self._append(run, line)

    def read_steps(self, run: Run) -> None:
        """Take the step events written since the last look (utils/step_events.py; one file per pytest worker)
        into the results: each test's steps, and the step it is on now."""
        with self._steps_lock:
            self._read_steps(run)

    def refresh_steps(self, run_id: str) -> None:
        """For pages following a run: catch up on its steps even while pytest prints nothing (a slow page)."""
        run = self._runs.get(run_id)
        if run is not None and run.active and not run.claimed_by \
                and time.monotonic() - run._steps_read_at > STEP_POLL_SECONDS:
            self.read_steps(run)

    def _follow_steps(self, run: Run) -> None:
        """Read the run's step events every STEP_POLL_SECONDS while it runs, so the step each test is on is
        published even while pytest prints nothing (a slow page)."""
        def follow() -> None:
            while run.active and run._proc is not None:
                time.sleep(STEP_POLL_SECONDS)
                if time.monotonic() - run._steps_read_at >= STEP_POLL_SECONDS:
                    try:
                        self.read_steps(run)
                    except Exception:  # noqa: BLE001 - following steps must never stop the run
                        logger.exception("Reading the steps of %s failed", run.id)
        threading.Thread(target=follow, name=f"steps-{run.id}", daemon=True).start()

    def _read_steps(self, run: Run) -> None:
        run._steps_read_at = time.monotonic()
        applied = False
        try:
            files = sorted(run.results_dir.glob("autoplay-steps-*.jsonl"))
        except OSError:
            return
        for path in files:
            start = run._step_offsets.get(path.name, 0)
            try:
                with path.open("rb") as f:
                    f.seek(start)
                    chunk = f.read()
            except OSError:
                continue
            complete = chunk.rfind(b"\n") + 1  # a line still being written waits for the next look
            run._step_offsets[path.name] = start + complete
            for line in chunk[:complete].decode("utf-8", "replace").splitlines():
                try:
                    self._apply_step(run, json.loads(line))
                    applied = True
                except (ValueError, TypeError, AttributeError):
                    continue
        if applied:
            self._publish(run, "step")

    def _apply_step(self, run: Run, evt: dict) -> None:
        if evt.get("event") != "step":
            return
        nodeid = str(evt.get("nodeid", ""))
        result = run.results.get(nodeid)
        if result is None:
            test_id, browser = self.split_nodeid(nodeid, run.config.browsers)
            result = run.results[nodeid] = TestResult(nodeid, test_id, browser, "running")
        n = int(evt.get("n") or 0)
        entry = next((s for s in result.steps if s.get("n") == n and s.get("status") == "running"), None)
        if evt.get("phase") == "start":
            if entry is None:  # a retried test runs its steps again: keep the latest attempt
                result.steps = [s for s in result.steps if s.get("n") < n]
                entry = {"n": n, "of": int(evt.get("of") or 0), "kind": str(evt.get("kind", "")),
                         "label": run.scrub(str(evt.get("label", ""))), "status": "running", "seconds": None,
                         "error": ""}
                result.steps.append(entry)
            if result.outcome == "running":
                result.step = f"{n} of {entry['of']}: {entry['label']}"
        elif evt.get("phase") == "end" and entry is not None:
            entry["status"] = "failed" if evt.get("status") == "failed" else "passed"
            entry["seconds"] = evt.get("seconds")
            entry["error"] = run.scrub(str(evt.get("error") or ""))

    def _finish(self, run: Run) -> None:
        """After pytest has ended (here or on a runner): status, reports, notifications, next queued run."""
        self.read_steps(run)  # the last ones (a remote run's arrive with its results)
        for r in run.results.values():
            r.step = ""
            for s in r.steps:  # a step still "running" when its test ended was cut short
                if s.get("status") == "running":
                    s["status"] = "failed" if r.outcome in ("failed", "error") else "stopped"
        try:
            from webapp.services.heals import after_run  # late: heals imports the page scanner, which imports us
            healed = after_run(run, self.workspace)
            if healed:
                self._append(run, f"[webapp] {healed} locator{'s were' if healed != 1 else ' was'} healed during "
                                  "this run: review them in History > Healed locators.")
        except Exception:  # heal bookkeeping must never break finishing a run
            logger.exception("Could not read the heal records of %s", run.id)
        run.paused = None
        run.status = self._final_status(run)
        run.finished_at = _now()
        # Tests still marked running never reported back (crash/cancel)
        for r in run.results.values():
            if r.outcome == "running":
                r.outcome = "cancelled" if run._cancelled else "error"
        self._append(run, f"[webapp] Finished: {run.status} (exit code {run.exit_code})")
        self._save(run)
        self.write_summary(run)
        self._generate_report(run)
        self._save(run)
        for listener in list(self.listeners):
            try:
                listener(run)
            except Exception:  # a listener must never break the run thread
                logger.exception("Run listener failed for %s", run.id)
        self._publish(run, "finished")
        self._dispatch()

    # ───────────── remote runners ─────────────

    def claim(self, runner: str) -> Run | None:
        """The next queued run for this runner, now marked running. A runner does one run at a time."""
        with self._lock:
            if any(r.status == "running" and r.claimed_by == runner for r in self._runs.values()):
                return None
            run = next((r for r in self._queue
                        if r.config.runner and self.runners and self.runners.accepts(r.config.runner, runner)), None)
            if run is None:
                return None
            self._queue.remove(run)
            run.status, run.started_at, run._last_seen, run.claimed_by = "running", _now(), time.monotonic(), runner
        self._append(run, f"[webapp] Started on runner {runner}")
        self._save(run)
        return run

    def remote_run(self, run_id: str, runner: str) -> Run | None:
        """The running run `run_id` if it was claimed by this runner."""
        run = self._runs.get(run_id)
        return run if run and run.status == "running" and run.claimed_by == runner and not run._finishing else None

    def remote_events(self, run: Run, lines: list[str]) -> bool:
        """Output lines from the runner. True when the run was cancelled and the runner should stop."""
        run._last_seen = time.monotonic()
        with (run.dir / "console.log").open("a", encoding="utf-8") as console:
            for raw in lines:
                self._ingest_line(run, console, raw)
        return run._cancelled

    def remote_results(self, run: Run, archive: Path) -> int:
        """Unpack the Allure results the runner uploaded (replaces any earlier upload)."""
        run._last_seen = time.monotonic()
        shutil.rmtree(run.results_dir, ignore_errors=True)
        return extract_safely(archive, run.results_dir)

    def remote_finish(self, run: Run, exit_code: int | None, error: str = "") -> bool:
        with self._lock:
            if run.status != "running" or run._finishing:
                return False
            run._finishing = True
        run.exit_code = exit_code
        if error:
            run.error = run.scrub(error)[:500]
            self._append(run, f"[webapp] ERROR: {run.error}")
        self._finish(run)
        return True

    def _ensure_watchdog(self) -> None:
        with self._lock:
            if self._watchdog and self._watchdog.is_alive():
                return
            self._watchdog = threading.Thread(target=self._watch, name="runner-watchdog", daemon=True)
            self._watchdog.start()

    def _watch(self) -> None:
        """Mark remote runs whose runner went silent as lost, so they do not stay 'running' for ever."""
        while True:
            time.sleep(WATCH_INTERVAL)
            remote = [r for r in list(self._runs.values()) if r.config.runner and r.active]
            if not remote:
                return
            now = time.monotonic()
            for run in remote:
                if run.status == "running" and now - run._last_seen > RUNNER_SILENCE_SECONDS:
                    self.remote_finish(run, None, f"Runner {run.claimed_by or run.config.runner} stopped responding.")

    def _final_status(self, run: Run) -> str:
        if run._cancelled:
            return "cancelled"
        if run.error or run.exit_code is None:
            return "error"
        if run.exit_code == 0:
            return "passed"
        if run.exit_code == 1:
            return "failed"
        if run.exit_code == 5:
            run.error = "No tests were collected for this system/tags selection."
        else:
            run.error = f"pytest exited with code {run.exit_code}."
        return "error"

    def _handle_event(self, run: Run, payload: str) -> None:
        try:
            evt = json.loads(payload)
        except ValueError:
            self._append(run, PREFIX + payload)
            return
        kind = evt.get("event")
        if kind == "collected":
            run.collected = int(evt.get("count") or 0)
            if run.collected:
                run.expected = run.collected
        elif kind == "debug":  # the test waits after a failure, or goes on (utils/debug_pause.py)
            if evt.get("phase") == "paused":
                run.paused = {**{k: (run.scrub(str(v)) if isinstance(v, str) else v) for k, v in evt.items()},
                              "at": _now()}
                if not evt.get("notice"):  # a pause told again because the edited files could not be read is the same
                    run.debug_pauses += 1
                self._append(run, f"[webapp] Paused: step {evt.get('step')} failed"
                                  + (f" on field '{evt.get('field')}'" if evt.get("field") else "")
                                  + ". The browser is still open; fix the test, then resume.")
            else:
                run.paused = None
                self._append(run, f"[webapp] Resumed ({evt.get('do')}).")
            self._publish(run, "paused" if run.paused else "resumed")
        elif kind == "retry":
            nodeid = str(evt.get("nodeid", ""))
            if nodeid in run.results:
                run.results[nodeid].retries = int(evt.get("attempt") or 0)
                run.results[nodeid].message = "failed; running it again"
        elif kind in ("start", "test"):
            nodeid = str(evt.get("nodeid", ""))
            test_id, browser = self.split_nodeid(nodeid, run.config.browsers)
            result = run.results.get(nodeid) or TestResult(nodeid, test_id, browser, "running")
            if kind == "test":
                outcome = evt.get("outcome", "")
                if outcome == "failed" and evt.get("when") != "call":
                    outcome = "error"
                result.outcome = outcome
                result.duration = float(evt.get("duration") or 0)
                result.message = run.scrub(str(evt.get("message") or ""))
                result.retries = int(evt.get("retries") or result.retries or 0)
                result.step = ""
            run.results[nodeid] = result
        if kind in ("collected", "retry", "start", "test"):
            self._publish(run, "result")

    @staticmethod
    def split_nodeid(nodeid: str, browsers: list[str]) -> tuple[str, str]:
        """'...::test_dynamic_execution[chrome-TC-1]' -> ('TC-1', 'chrome')."""
        params = nodeid[nodeid.find("[") + 1:-1] if nodeid.endswith("]") and "[" in nodeid else nodeid
        for b in sorted(browsers, key=len, reverse=True):
            if params.startswith(b + "-"):
                return params[len(b) + 1:], b
            if params.endswith("-" + b):
                return params[:-len(b) - 1], b
        return params, browsers[0] if len(browsers) == 1 else ""

    def write_summary(self, run: Run) -> Path | None:
        """(Re)write the run's built-in report.html from its results and the run history."""
        if run.active:
            return None
        try:
            details = RunArtifacts().details(run.id, run.results_dir)
            content = build_report(run, list(self._runs.values()), details)
            tmp = run.dir / "report.html.tmp"
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(run.summary_file)
        except OSError:
            logger.exception("Could not write the report of run %s", run.id)
            return None
        return run.summary_file

    def _generate_report(self, run: Run) -> None:
        if not run.results_dir.is_dir() or not any(run.results_dir.iterdir()):
            run.report_status, run.report_message = "unavailable", "No Allure results were produced."
            return
        exe = shutil.which(self.allure_cmd)
        if not exe:
            run.report_status = "unavailable"
            run.report_message = (f"Allure CLI '{self.allure_cmd}' not found. Install it (e.g. `scoop install allure`) "
                                  f"or set AUTOPLAY_ALLURE_CMD. Raw results: {run.results_dir}")
            self._append(run, f"[webapp] {run.report_message}")
            return
        run.report_status = "generating"
        self._append(run, "[webapp] Generating Allure report...")
        try:
            proc = subprocess.run(
                [exe, "generate", str(run.results_dir), "-o", str(run.report_dir), "--clean"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            run.report_status, run.report_message = "failed", str(e)
        else:
            if proc.returncode == 0 and (run.report_dir / "index.html").exists():
                run.report_status, run.report_message = "ready", ""
            else:
                run.report_status = "failed"
                run.report_message = (proc.stderr or proc.stdout).strip()[-500:]
        self._append(run, f"[webapp] Allure report: {run.report_status} {run.report_message}".rstrip())

    def _append(self, run: Run, line: str) -> None:
        run.log.append(line)
        overflow = len(run.log) - MAX_LOG_LINES
        if overflow > 0:
            del run.log[:overflow]
            run.log_offset += overflow
        now = time.monotonic()
        if now - run._log_published >= LOG_PUBLISH_SECONDS:  # console text is told in batches, not per line
            run._log_published = now
            self._publish(run, "log")

    def _save(self, run: Run) -> None:
        try:
            with self._save_lock:
                tmp = run.dir / "run.json.tmp"
                tmp.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
                tmp.replace(run.dir / "run.json")
                mtime = (run.dir / "run.json").stat().st_mtime_ns
        except OSError:
            logger.exception("Could not save run %s", run.id)
            return
        self._index_call("put", run, mtime)
        self._publish(run, "saved")

    def _publish(self, run: Run, change: str) -> None:
        self.bus.publish("run", id=run.id, change=change, status=run.status, system=run.config.system,
                         env=run.config.env, trigger=run.trigger)

    def _open_index(self) -> ResultsIndex | None:
        """The results index; an unreadable one is set aside and rebuilt from the run folders."""
        path = index_path(self.runs_dir)
        for attempt in (1, 2):
            try:
                return ResultsIndex(path)
            except Exception:  # noqa: BLE001 - history still works from the folders
                if attempt == 2:
                    logger.exception("Could not open the results index; history queries will filter in memory")
                    return None
                logger.warning("Results index unreadable; rebuilding it from the run folders", exc_info=True)
                for suffix in ("", "-wal", "-shm"):
                    try:
                        Path(f"{path}{suffix}").replace(f"{path}{suffix}.bad")
                    except OSError:
                        pass
        return None

    def _index_call(self, method: str, *args) -> None:
        if self.index is None:
            return
        try:
            getattr(self.index, method)(*args)
        except Exception:  # noqa: BLE001
            logger.exception("Results index %s failed", method)

    def _load_history(self) -> None:
        if self.index is not None:
            try:
                counts = self.index.sync(self.runs_dir)
                if counts["added"] or counts["updated"] or counts["removed"]:
                    logger.info("Results index synced: %s", counts)
            except Exception:  # noqa: BLE001
                logger.exception("Results index sync failed")
        for meta in self.runs_dir.glob("*/run.json"):
            try:
                run = run_from_dict(json.loads(meta.read_text(encoding="utf-8")), meta.parent)
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("Skipping unreadable run metadata: %s", meta)
                continue
            if run.active:  # app stopped mid-run
                run.status, run.error = "error", "Web app stopped while this run was in progress."
                self._index_call("put", run, 0)
            log_file = meta.parent / "console.log"
            if log_file.exists():
                run.log = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_LOG_LINES:]
            self._runs[run.id] = run

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=30)
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            proc.kill()
