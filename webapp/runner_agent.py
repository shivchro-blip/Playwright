"""Runner agent: runs this app's queued tests on this machine.

    python -m webapp.runner_agent --server https://autoplay.example.com --token apr_...

It polls the app (outbound HTTPS only), takes the next run queued for its runner, downloads the test definitions
(Data, Pages, Tests), runs pytest here, streams the console back, then uploads the Allure results. Login values
come from this machine's own .env (--env-file) or environment, plus any typed into the app for that run.
Run the same version of the code here as on the app (git pull on both).
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from utils.workspace import REPO_ROOT, resolve_dotenv_path
from webapp.services.archives import ArchiveError, extract_safely, zip_folder
from webapp.services.run_manager import Run, RunConfig, RunManager, pytest_command, pytest_environment
from webapp.services.runners import PROTOCOL

FLUSH_SECONDS = 1.0
MAX_BATCH = 2000
LOST_AFTER = 120  # seconds without reaching the app before the run is abandoned


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


def log(message: str) -> None:
    print(f"[runner {time.strftime('%H:%M:%S')}] {message}", flush=True)


class Client:
    def __init__(self, server: str, token: str, timeout: float = 60):
        self.base = server.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None, data=None, length: int | None = None):
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = None
        if body is not None:
            payload, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif data is not None:
            payload, headers["Content-Type"] = data, "application/zip"
            if length is not None:
                headers["Content-Length"] = str(length)
        request = urllib.request.Request(self.base + path, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return response.status, raw
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass
            raise ApiError(e.code, str(detail)) from None

    def json(self, method: str, path: str, body: dict | None = None) -> dict | None:
        status, raw = self.call(method, path, body)
        return json.loads(raw) if status != 204 and raw else None

    def download(self, path: str, target: Path) -> None:
        _, raw = self.call("GET", path)
        target.write_bytes(raw)


def _output_reader(proc: subprocess.Popen, lines: queue.Queue) -> None:
    for raw in proc.stdout:
        lines.put(raw.rstrip("\r\n"))
    lines.put(None)


def execute(client: Client, claimed: dict, env_file: Path | None, python_exe: str = sys.executable) -> str:
    """Run one claimed run to the end. Returns "done" or "lost" (the app could not be reached)."""
    run_id = claimed["run"]["id"]
    work = Path(tempfile.mkdtemp(prefix="autoplay-runner-"))
    try:
        run = Run(id=run_id, config=RunConfig(**claimed["run"]["config"]), dir=work / "run")
        run.dir.mkdir()
        run._secrets = dict(claimed["run"].get("secrets") or {})
        log(f"Run {run_id}: {run.config.system}/{run.config.env} tags={','.join(run.config.tags)}")
        archive = work / "workspace.zip"
        client.download(f"/runner-api/runs/{run_id}/workspace", archive)
        workspace = work / "workspace"
        extract_safely(archive, workspace)
        env = pytest_environment(run, workspace)
        if env_file and env_file.is_file():
            env["AUTOPLAY_DOTENV_PATH"] = str(env_file)
        command = pytest_command(run, python_exe, REPO_ROOT)

        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" \
            else {"start_new_session": True}
        error = ""
        lines: queue.Queue = queue.Queue()
        pending: list[str] = []
        proc = None
        try:
            proc = subprocess.Popen(command, cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1, **flags)
            threading.Thread(target=_output_reader, args=(proc, lines), daemon=True).start()
        except OSError as e:
            error = f"Could not start pytest: {e}"
        last_ok, finished, killed = time.monotonic(), proc is None, False
        while not finished or pending:
            deadline = time.monotonic() + FLUSH_SECONDS
            while not finished and time.monotonic() < deadline and len(pending) < MAX_BATCH:
                try:
                    item = lines.get(timeout=max(0.05, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if item is None:
                    finished = True
                else:
                    pending.append(run.scrub(item))
            batch = pending[:MAX_BATCH]
            try:
                reply = client.json("POST", f"/runner-api/runs/{run_id}/events", {"lines": batch})
                del pending[:len(batch)]
                last_ok = time.monotonic()
                if reply and reply.get("cancel") and proc and proc.poll() is None and not killed:
                    log("Cancelled from the app: stopping.")
                    RunManager._kill_tree(proc)
                    killed = True
            except ApiError as e:
                if e.status == 404:
                    log(f"The app no longer has run {run_id} for this runner ({e.detail}): stopping.")
                    if proc and proc.poll() is None:
                        RunManager._kill_tree(proc)
                    return "lost"
                log(f"Could not send output: {e}")
            except OSError as e:
                log(f"Could not reach the app: {e}")
            if time.monotonic() - last_ok > LOST_AFTER:
                log(f"The app has been unreachable for {LOST_AFTER}s: abandoning the run.")
                if proc and proc.poll() is None:
                    RunManager._kill_tree(proc)
                return "lost"
        exit_code = proc.wait() if proc else None

        results = run.results_dir
        if results.is_dir() and any(results.iterdir()):
            zipped = zip_folder(results, work / "results.zip")
            with zipped.open("rb") as handle:
                try:
                    client.call("POST", f"/runner-api/runs/{run_id}/results", data=handle, length=zipped.stat().st_size)
                except (ApiError, OSError, ArchiveError) as e:
                    error = error or f"Could not upload the results: {e}"
                    log(error)
        client.json("POST", f"/runner-api/runs/{run_id}/finish", {"exit_code": exit_code, "error": error})
        log(f"Run {run_id}: finished with exit code {exit_code}")
        return "done"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m webapp.runner_agent", description=__doc__.split("\n")[0])
    parser.add_argument("--server", default=os.environ.get("AUTOPLAY_SERVER", ""), help="the app's URL")
    parser.add_argument("--token", default=os.environ.get("AUTOPLAY_RUNNER_TOKEN", ""),
                        help="runner token (python -m webapp.runners add NAME); or AUTOPLAY_RUNNER_TOKEN")
    parser.add_argument("--env-file", help="this machine's .env with the login values (default: the workspace .env)")
    parser.add_argument("--poll", type=float, default=3.0, help="seconds between checks for work")
    parser.add_argument("--once", action="store_true", help="exit after one run")
    parser.add_argument("--idle-exit", type=float, default=0, help="exit after this many seconds without work")
    parser.add_argument("--allow-insecure", action="store_true",
                        help="allow plain http to a non-local server (login values travel unencrypted)")
    args = parser.parse_args(argv)
    if not args.server or not args.token:
        print("Give --server and --token (or AUTOPLAY_SERVER and AUTOPLAY_RUNNER_TOKEN).", file=sys.stderr)
        return 2
    parts = urlsplit(args.server)
    local = (parts.hostname or "") in ("127.0.0.1", "localhost", "::1")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        print("--server must be a full http(s) URL.", file=sys.stderr)
        return 2
    if parts.scheme == "http" and not local and not args.allow_insecure:
        print("Refusing plain http to a remote server: login values for runs would travel unencrypted. Use https, "
              "or --allow-insecure on a network you trust.", file=sys.stderr)
        return 2
    env_file = Path(args.env_file) if args.env_file else Path(resolve_dotenv_path(str(REPO_ROOT / "workspace")))
    client = Client(args.server, args.token)
    log(f"Waiting for work from {args.server}")
    idle_since, delay = time.monotonic(), args.poll
    while True:
        try:
            claimed = client.json("POST", "/runner-api/claim", {"protocol": PROTOCOL})
            delay = args.poll
        except ApiError as e:
            if e.status in (401, 409):
                print(f"The app refused this runner: {e.detail}", file=sys.stderr)
                return 2
            log(f"Claim failed: {e}")
            claimed, delay = None, min(delay * 2, 30)
        except OSError as e:
            log(f"Could not reach the app: {e}")
            claimed, delay = None, min(delay * 2, 30)
        if claimed:
            try:
                execute(client, claimed, env_file)
            except (ApiError, OSError) as e:
                log(f"Run failed: {e}")
            idle_since = time.monotonic()
            if args.once:
                return 0
            continue
        if args.idle_exit and time.monotonic() - idle_since > args.idle_exit:
            return 0
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main())
