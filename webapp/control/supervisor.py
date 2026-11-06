"""Starts the tenants the control plane manages, each as its own `python -m webapp` process, and restarts them
when they stop (waiting longer after each crash, up to a minute)."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from utils.workspace import REPO_ROOT
from webapp.control.tenants import Tenant, TenantStore

logger = logging.getLogger(__name__)
CHECK_SECONDS = 3
MAX_BACKOFF = 60


@dataclass
class ProcessState:
    process: subprocess.Popen | None = None
    started: float = 0
    restarts: int = 0
    last_exit: int | None = None
    next_try: float = 0
    log: list[str] = field(default_factory=list)


def child_env(tenant: Tenant) -> dict[str, str]:
    """The tenant's process sees only its own settings: no AUTOPLAY_* of the control plane or another tenant."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AUTOPLAY_", "PYTEST_", "TENANT_"))}
    workspace = Path(tenant.workspace)
    env.update({"AUTOPLAY_WORKSPACE": str(workspace), "AUTOPLAY_DOTENV_PATH": str(workspace / ".env"),
                "AUTOPLAY_HOST": "127.0.0.1", "AUTOPLAY_PORT": str(tenant.port), "AUTOPLAY_AUTH_ENABLED": "true",
                "AUTOPLAY_PUBLIC_URL": tenant.url, "AUTOPLAY_EXIT_WITH_PARENT": "true", "PYTHONUNBUFFERED": "1"})
    return env


def _close(process: subprocess.Popen) -> None:
    if process.stdin:
        try:
            process.stdin.close()
        except OSError:
            pass


class Supervisor:
    def __init__(self, store: TenantStore, python_exe: str, logs: Path):
        self.store, self.python_exe, self.logs = store, python_exe, Path(logs)
        self.states: dict[str, ProcessState] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def state(self, tid: str) -> dict:
        with self._lock:
            s = self.states.get(tid)
            alive = bool(s and s.process and s.process.poll() is None)
            return {"process": "running" if alive else ("crashed" if s and s.last_exit not in (None, 0) else
                                                        "stopped"),
                    "pid": s.process.pid if alive else None, "restarts": s.restarts if s else 0,
                    "last_exit": s.last_exit if s else None,
                    "uptime_s": round(time.time() - s.started) if alive else 0}

    def start(self, tenant: Tenant) -> None:
        with self._lock:
            s = self.states.setdefault(tenant.id, ProcessState())
            if s.process and s.process.poll() is None:
                return
            self.logs.mkdir(parents=True, exist_ok=True)
            log = (self.logs / f"{tenant.id}.log").open("a", encoding="utf-8")
            # its stdin is a pipe from this process: when the control plane goes away, however it ends, the
            # pipe closes and the tenant stops too (AUTOPLAY_EXIT_WITH_PARENT)
            s.process = subprocess.Popen([self.python_exe, "-m", "webapp"], cwd=REPO_ROOT, env=child_env(tenant),
                                         stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            log.close()
            s.started = time.time()
            logger.info("Started tenant %s (pid %s, port %s)", tenant.id, s.process.pid, tenant.port)

    def stop(self, tid: str, timeout: float = 10) -> None:
        with self._lock:
            s = self.states.get(tid)
            if not s or not s.process or s.process.poll() is not None:
                return
            s.process.terminate()
            try:
                s.process.wait(timeout)
            except subprocess.TimeoutExpired:
                s.process.kill()
                s.process.wait(5)
            _close(s.process)
            s.last_exit = 0  # stopped on purpose

    def stop_all(self) -> None:
        self._stop.set()
        for tid in list(self.states):
            self.stop(tid)

    def check(self) -> None:
        """One pass: start what should run, restart what stopped (with back-off), stop what should not."""
        now = time.time()
        wanted = {t.id: t for t in self.store.list() if t.managed}
        for tid in list(self.states):
            if tid not in wanted or not wanted[tid].running:
                self.stop(tid)
        for tid, tenant in wanted.items():
            if not tenant.running:
                continue
            with self._lock:
                s = self.states.setdefault(tid, ProcessState())
                code = s.process.poll() if s.process else None
                if s.process is not None and code is None:
                    continue
                if s.process is not None:  # it was running and stopped by itself
                    _close(s.process)
                    s.last_exit, s.process = code, None
                    s.restarts += 1
                    s.next_try = now + min(MAX_BACKOFF, 2 ** min(s.restarts, 6))
                    logger.warning("Tenant %s exited with %s; restarting", tid, code)
                if now < s.next_try:
                    continue
            try:
                self.start(tenant)
            except OSError as e:
                logger.error("Could not start tenant %s: %s", tid, e)

    def run(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.check()
                except Exception:
                    logger.exception("Supervisor pass failed")
                self._stop.wait(CHECK_SECONDS)

        self._thread = threading.Thread(target=loop, name="tenant-supervisor", daemon=True)
        self._thread.start()
