"""Deploy-aware releases: confidence that arrives with the deploy.

Your CI/CD pipeline announces a deploy (POST /api/v1/deploys). AutoPlay then, on its own:
1. runs the system's journeys (or the ones named) on that environment;
2. when the run finishes, makes a release check from that run's evidence (webapp/services/releases.py, counting
   only results since the deploy), so every deploy carries an evidence pack;
3. reports GO or NO-GO: to the pipeline's callback URL, and as a commit status on GitHub (a "pending" status at
   once, then success or failure with a link to the evidence) when the deploy names the repository and commit;
4. on GO, starts a check of each monitor watching that system and environment.

Each deploy and what became of it is kept in reports/deploys.json. Runs a deploy starts carry the trigger
"deploy:<id>", so the flow picks up their end from the event bus (and an automation rule waiting for a deploy still
fires on the same announcement).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

TRIGGER = "deploy:"
KEEP = 200
STATUS_CONTEXT = "AutoPlay release check"


class DeployError(ValueError):
    """Shown to the caller as is."""


@dataclass
class Deploy:
    id: str
    system: str
    env: str
    build: str = ""
    by: str = ""
    at: str = ""
    journeys: list[str] = field(default_factory=list)
    callback_url: str = ""
    repo: str = ""
    sha: str = ""
    run_id: str = ""
    status: str = "running"  # running | decided | failed
    decision: str = ""  # go | no-go
    release_id: str = ""
    reasons: list[str] = field(default_factory=list)
    reported: list[dict] = field(default_factory=list)  # {to, ok, detail}
    monitors: list[str] = field(default_factory=list)  # monitor checks started on GO
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def check_request(system: str, env: str, callback_url: str = "", repo: str = "", sha: str = "") -> None:
    if callback_url:
        parts = urlsplit(callback_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise DeployError("callback_url must be a full http(s) address.")
    if repo and not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo):
        raise DeployError("repo must look like owner/repo.")
    if sha and not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        raise DeployError("sha must be a commit hash.")
    if bool(repo) != bool(sha):
        raise DeployError("Give both repo and sha for a commit status, or neither.")


class DeployStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def list(self, limit: int = 50) -> list[Deploy]:
        known = {f.name for f in fields(Deploy)}
        return [Deploy(**{k: v for k, v in d.items() if k in known}) for d in reversed(self._read()[-limit:])]

    def get(self, did: str) -> Deploy | None:
        return next((d for d in self.list(KEEP) if d.id == did), None)

    def save(self, deploy: Deploy) -> Deploy:
        with self._lock:
            items = [d for d in self._read() if d.get("id") != deploy.id] + [deploy.to_dict()]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(items[-KEEP:], indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        return deploy


class DeployFlow:
    """`start_journeys(system, ids, env, trigger)` starts the run; `make_release(deploy) -> pack`;
    `report_url(pack) -> str`; `post(url, payload, headers) -> (status, text)`; `lookup(key)` reads .env;
    `check_monitors(system, env) -> [monitor ids started]`."""

    def __init__(self, store: DeployStore, *, start_journeys: Callable, make_release: Callable[[Deploy], dict],
                 report_url: Callable[[dict], str], post: Callable[[str, dict, dict], tuple[int, str]],
                 lookup: Callable[[str], str | None], check_monitors: Callable[[str, str], list[str]],
                 publish: Callable[..., object] | None = None):
        self.store, self.start_journeys, self.make_release = store, start_journeys, make_release
        self.report_url, self.post, self.lookup, self.check_monitors = report_url, post, lookup, check_monitors
        self.publish = publish or (lambda *a, **k: None)
        self._worker_lock = threading.Lock()

    # ───── 1: the deploy is announced ─────

    def start(self, system: str, env: str, *, build: str = "", by: str = "", journeys: list[str] | None = None,
              callback_url: str = "", repo: str = "", sha: str = "") -> Deploy:
        check_request(system, env, callback_url, repo, sha)
        deploy = Deploy(f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}", system, env, build.strip()[:80],
                        by.strip()[:80], time.strftime("%Y-%m-%dT%H:%M:%S"), [j for j in journeys or [] if j],
                        callback_url, repo, sha)
        self._status(deploy, "pending", f"Running the journeys on {env}...")
        try:
            run = self.start_journeys(system, deploy.journeys, env, TRIGGER + deploy.id)
        except Exception as e:  # noqa: BLE001 - no journeys, a full queue: the deploy says why
            deploy.status, deploy.error = "failed", str(e)
            self.store.save(deploy)
            self._status(deploy, "error", f"Could not start the journeys: {e}"[:140])
            self._callback(deploy, None)
            raise DeployError(str(e)) from e
        deploy.run_id = getattr(run, "id", "")
        self.store.save(deploy)
        self.publish("deploy", system=system, env=env, build=deploy.build, by=deploy.by, id=deploy.id)
        return deploy

    # ───── 2-4: the run finished ─────

    def on_event(self, event: dict) -> None:
        """Event bus subscriber: a deploy's run finished (the work happens on its own thread)."""
        if event.get("topic") != "run" or event.get("change") != "finished":
            return
        trigger = str(event.get("trigger") or "")
        if not trigger.startswith(TRIGGER):
            return
        threading.Thread(target=self.finish, args=(trigger[len(TRIGGER):],), name="deploy-release",
                         daemon=True).start()

    def finish(self, deploy_id: str) -> Deploy | None:
        with self._worker_lock:
            deploy = self.store.get(deploy_id)
            if deploy is None or deploy.status != "running":
                return deploy
            try:
                pack = self.make_release(deploy)
            except Exception as e:  # noqa: BLE001 - say what went wrong, to the pipeline too
                deploy.status, deploy.error = "failed", str(e)
                self.store.save(deploy)
                self._status(deploy, "error", f"The release check failed: {e}"[:140])
                self._callback(deploy, None)
                return deploy
            deploy.status, deploy.decision, deploy.release_id = "decided", pack["decision"], pack["id"]
            deploy.reasons = list(pack.get("reasons") or [])
            go = pack["decision"] == "go"
            self._status(deploy, "success" if go else "failure",
                         ("GO" if go else "NO-GO") + (f": {deploy.reasons[0]}" if deploy.reasons else ""),
                         self.report_url(pack))
            self._callback(deploy, pack)
            if go:
                try:
                    deploy.monitors = list(self.check_monitors(deploy.system, deploy.env))
                except Exception:  # noqa: BLE001 - monitors are a follow-up, not the decision
                    logger.warning("Could not start monitor checks after deploy %s", deploy.id, exc_info=True)
            self.store.save(deploy)
            return deploy

    # ───── reporting ─────

    def _callback(self, deploy: Deploy, pack: dict | None) -> None:
        if not deploy.callback_url:
            return
        payload = {"deploy": deploy.id, "system": deploy.system, "env": deploy.env, "build": deploy.build,
                   "status": deploy.status, "decision": deploy.decision, "reasons": deploy.reasons,
                   "error": deploy.error, "run": deploy.run_id, "release": deploy.release_id,
                   "report": self.report_url(pack) if pack else ""}
        self._send(deploy, "callback", deploy.callback_url, payload, {"Content-Type": "application/json"})

    def _status(self, deploy: Deploy, state: str, description: str, target_url: str = "") -> None:
        if not (deploy.repo and deploy.sha):
            return
        token = (self.lookup("RELEASE_GITHUB_TOKEN") or self.lookup("ISSUE_GITHUB_TOKEN") or "").strip()
        if not token:
            deploy.reported.append({"to": "github", "ok": False,
                                    "detail": "No RELEASE_GITHUB_TOKEN (or ISSUE_GITHUB_TOKEN) in .env."})
            return
        api = (self.lookup("RELEASE_GITHUB_API_URL") or self.lookup("ISSUE_GITHUB_API_URL")
               or "https://api.github.com").rstrip("/")
        body = {"state": state, "context": STATUS_CONTEXT, "description": description[:140]}
        if target_url:
            body["target_url"] = target_url
        self._send(deploy, f"github {state}", f"{api}/repos/{deploy.repo}/statuses/{deploy.sha}", body,
                   {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def _send(self, deploy: Deploy, to: str, url: str, payload: dict, headers: dict) -> None:
        try:
            status, text = self.post(url, payload, headers)
            ok = 200 <= status < 300
            deploy.reported.append({"to": to, "ok": ok, "detail": f"HTTP {status}" + ("" if ok else f": {text[:200]}")})
        except Exception as e:  # noqa: BLE001 - an unreachable pipeline must not lose the decision
            deploy.reported.append({"to": to, "ok": False, "detail": str(e)[:200]})


def http_post(url: str, payload: dict, headers: dict) -> tuple[int, str]:
    import httpx
    response = httpx.post(url, json=payload, headers=headers, timeout=15)
    return response.status_code, response.text
