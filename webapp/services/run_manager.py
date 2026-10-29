"""Runs the framework as background jobs: one pytest subprocess per run, own folder per run.

runs/<id>/
    run.json          status, config, per-test results (history)
    console.log       full console output
    allure-results/   raw allure-pytest output
    allure-report/    generated HTML report (if the Allure CLI is available)
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
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from webapp.pytest_events import PREFIX
from webapp.services.env_config import format_value, is_secret

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 20_000
SUITE = "tests/dynamic_test_suite.py"
ACTIVE = ("queued", "running")


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


@dataclass
class TestResult:
    nodeid: str
    test_id: str
    browser: str
    outcome: str  # running | passed | failed | skipped | error | xfailed | xpassed | cancelled
    duration: float = 0.0
    message: str = ""


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
    results: dict[str, TestResult] = field(default_factory=dict)
    log: list[str] = field(default_factory=list, repr=False)
    log_offset: int = 0  # lines dropped from the front of `log` (memory cap)
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _cancelled: bool = field(default=False, repr=False)

    @property
    def results_dir(self) -> Path:
        return self.dir / "allure-results"

    @property
    def report_dir(self) -> Path:
        return self.dir / "allure-report"

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
            "results": [asdict(r) for r in list(self.results.values())],
        }


class RunManager:
    def __init__(self, repo_root: Path, workspace: Path, runs_dir: Path, python_exe: str,
                 allure_cmd: str = "allure", max_concurrent: int = 1):
        self.repo_root = Path(repo_root)
        self.workspace = Path(workspace)
        self.runs_dir = Path(runs_dir)
        self.python_exe = python_exe
        self.allure_cmd = allure_cmd
        self.max_concurrent = max_concurrent
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._load_history()

    # ───────────── queries ─────────────

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def allure_available(self) -> bool:
        return shutil.which(self.allure_cmd) is not None

    # ───────────── commands ─────────────

    def start(self, config: RunConfig, expected: int = 0) -> Run:
        config.tags = [t.strip() for t in config.tags if t and t.strip()]
        config.browsers = [b.strip().lower() for b in config.browsers if b and b.strip()] or ["chrome"]
        config.workers = max(1, int(config.workers or 1))
        if not config.system:
            raise ValueError("Select a system.")
        if not config.tags:
            raise ValueError("Select at least one tag - the framework runs only tagged tests.")
        bad = [k for k in config.options if is_secret(k)]
        if bad:
            raise ValueError(f"Credentials cannot be passed as run options: {', '.join(bad)}")

        with self._lock:
            active = [r for r in self._runs.values() if r.active]
            if len(active) >= self.max_concurrent:
                raise RuntimeError(f"Run {active[0].id} is still in progress.")
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            run = Run(id=run_id, config=config, dir=self.runs_dir / run_id, expected=expected)
            run.dir.mkdir(parents=True)
            self._runs[run_id] = run

        thread = threading.Thread(target=self._execute, args=(run,), name=f"run-{run_id}", daemon=True)
        thread.start()
        return run

    def cancel(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if not run or not run.active:
            return False
        run._cancelled = True
        proc = run._proc
        if proc and proc.poll() is None:
            self._kill_tree(proc)
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
        with self._lock:
            self._runs.pop(run_id, None)
        return True

    def rerun(self, run_id: str) -> Run:
        old = self._runs.get(run_id)
        if not old:
            raise ValueError("Run not found.")
        cfg = old.config
        config = RunConfig(system=cfg.system, env=cfg.env, tags=list(cfg.tags), browsers=list(cfg.browsers),
                           workers=cfg.workers, options=dict(cfg.options), test_ids=list(cfg.test_ids))
        return self.start(config, expected=old.collected or old.expected)

    # ───────────── internals ─────────────

    def _command(self, run: Run) -> list[str]:
        cfg = run.config
        cmd = [
            self.python_exe, "-m", "pytest", SUITE,
            "-p", "webapp.pytest_events",
            # Ignore the local (gitignored) pytest.ini addopts - they need plugins not
            # every machine has - and set what the web app relies on explicitly.
            "-o", "addopts=",
            "-o", "log_cli=true",
            "-o", "log_cli_level=INFO",
            "-v",
            f"--rootdir={self.repo_root}",
            f"--alluredir={run.results_dir}",
            "--clean-alluredir",
        ]
        if cfg.workers > 1:
            cmd += ["-n", str(cfg.workers)]
        else:
            cmd.append("-s")  # stream framework prints live
        return cmd

    def _environment(self, run: Run) -> dict[str, str]:
        cfg = run.config
        env = dict(os.environ)
        # The suite loads .env with override=False, so these per-run values win
        env.update({k: format_value(v) for k, v in cfg.options.items()})
        env.update({
            "systemConfig": cfg.system,
            "envConfig": cfg.env,
            "runTags": ",".join(cfg.tags),
            "browsers": ",".join(cfg.browsers),
            "runTestIds": ",".join(cfg.test_ids),
            "del_allure": "false",  # each run has its own fresh results folder
            "reportsDir": str(run.results_dir),
            "AUTOMATENY_WORKSPACE": str(self.workspace),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        return env

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
            for raw in run._proc.stdout:
                line = raw.rstrip("\r\n")
                # `-v` prints the node id before the outcome, so an event can start mid-line
                idx = line.find(PREFIX)
                if idx >= 0:
                    self._handle_event(run, line[idx + len(PREFIX):])
                    line = line[:idx]
                    if not line.strip():
                        continue
                console.write(line + "\n")
                self._append(run, line)
            run.exit_code = run._proc.wait()
        except Exception as e:  # spawn failure etc.
            logger.exception("Run %s failed", run.id)
            run.error = str(e)
            self._append(run, f"[webapp] ERROR: {e}")
        finally:
            console.close()
            run._proc = None

        run.status = self._final_status(run)
        run.finished_at = _now()
        # Tests still marked running never reported back (crash/cancel)
        for r in run.results.values():
            if r.outcome == "running":
                r.outcome = "cancelled" if run._cancelled else "error"
        self._append(run, f"[webapp] Finished: {run.status} (exit code {run.exit_code})")
        self._save(run)
        self._generate_report(run)
        self._save(run)

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
                result.message = str(evt.get("message") or "")
            run.results[nodeid] = result

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

    def _generate_report(self, run: Run) -> None:
        if not run.results_dir.is_dir() or not any(run.results_dir.iterdir()):
            run.report_status, run.report_message = "unavailable", "No Allure results were produced."
            return
        exe = shutil.which(self.allure_cmd)
        if not exe:
            run.report_status = "unavailable"
            run.report_message = (f"Allure CLI '{self.allure_cmd}' not found. Install it (e.g. `scoop install allure`) "
                                  f"or set AUTOMATENY_ALLURE_CMD. Raw results: {run.results_dir}")
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

    def _save(self, run: Run) -> None:
        try:
            tmp = run.dir / "run.json.tmp"
            tmp.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
            tmp.replace(run.dir / "run.json")
        except OSError:
            logger.exception("Could not save run %s", run.id)

    def _load_history(self) -> None:
        for meta in self.runs_dir.glob("*/run.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                run = Run(
                    id=data["id"], config=RunConfig(**data["config"]), dir=meta.parent,
                    expected=data.get("expected", 0), status=data.get("status", "error"),
                    created_at=data.get("created_at", ""), started_at=data.get("started_at", ""),
                    finished_at=data.get("finished_at", ""), exit_code=data.get("exit_code"),
                    error=data.get("error", ""), collected=data.get("collected"),
                    report_status=data.get("report_status", ""), report_message=data.get("report_message", ""),
                    results={r["nodeid"]: TestResult(**r) for r in data.get("results", [])},
                )
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("Skipping unreadable run metadata: %s", meta)
                continue
            if run.active:  # app stopped mid-run
                run.status, run.error = "error", "Web app stopped while this run was in progress."
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
