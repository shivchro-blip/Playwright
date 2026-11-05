"""The calls a remote runner makes to this app (see webapp/services/runners.py and webapp/runner_agent.py).

Each call carries the runner's token (Authorization: Bearer apr_...). A runner can only touch the run it claimed.
Secrets typed into the app for a run (login values) go to the runner in the claim response, so use HTTPS.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from webapp.services.archives import ArchiveError, zip_workspace
from webapp.services.run_manager import Run
from webapp.services.runners import PROTOCOL
from webapp.state import services

logger = logging.getLogger(__name__)

MAX_LINES, MAX_LINE = 5000, 20_000
MAX_UPLOAD = 1024**3
MAX_FAILURES, WINDOW = 20, 300  # refused tokens per client per 5 minutes
_failures: dict[str, deque] = defaultdict(deque)

router = APIRouter(prefix="/runner-api", tags=["runner"], include_in_schema=False)


def runner_name(request: Request) -> str:
    client = request.client.host if request.client else "?"
    recent = _failures[client]
    while recent and time.time() - recent[0] > WINDOW:
        recent.popleft()
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(429, "Too many refused tokens; wait a few minutes.")
    auth = request.headers.get("authorization", "")
    name = services().runners.by_token(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if not name:
        recent.append(time.time())
        raise HTTPException(401, "Send the runner token: Authorization: Bearer apr_...")
    services().runners.touch(name)
    return name


def _claimed(run_id: str, name: str) -> Run:
    run = services().runs.remote_run(run_id, name)
    if run is None:
        raise HTTPException(404, "No such run for this runner (finished, cancelled or taken by another).")
    return run


class ClaimRequest(BaseModel):
    protocol: int = 0
    host: str = ""
    version: str = ""


class EventsRequest(BaseModel):
    lines: list[str] = Field(default_factory=list)


class FinishRequest(BaseModel):
    exit_code: int | None = None
    error: str = ""


@router.post("/claim")
def claim(body: ClaimRequest, name: str = Depends(runner_name)):
    if body.protocol != PROTOCOL:
        raise HTTPException(409, f"This runner speaks protocol {body.protocol}, the app {PROTOCOL}: "
                                 "update the runner (git pull) so both run the same version.")
    services().runners.touch(name, {"host": body.host, "version": body.version})
    run = services().runs.claim(name)
    if run is None:
        return Response(status_code=204)
    services().audit.record(f"runner:{name}", "runner.claim", run.id)
    return {"run": {"id": run.id, "config": asdict(run.config), "secrets": dict(run._secrets)}}


@router.get("/runs/{run_id}/workspace")
def workspace(run_id: str, name: str = Depends(runner_name)):
    _claimed(run_id, name)
    archive = zip_workspace(services().settings.workspace)
    return FileResponse(archive, media_type="application/zip", filename="workspace.zip",
                        background=BackgroundTask(lambda: archive.unlink(missing_ok=True)))


@router.post("/runs/{run_id}/events")
def events(run_id: str, body: EventsRequest, name: str = Depends(runner_name)) -> dict:
    run = _claimed(run_id, name)
    lines = [line[:MAX_LINE] for line in body.lines[:MAX_LINES]]
    return {"cancel": services().runs.remote_events(run, lines)}


@router.post("/runs/{run_id}/results")
async def results(run_id: str, request: Request, name: str = Depends(runner_name)) -> dict:
    run = _claimed(run_id, name)
    handle, tmp = tempfile.mkstemp(suffix=".zip", prefix="autoplay-results-")
    size = 0
    try:
        with os.fdopen(handle, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "The results archive is too large.")
                out.write(chunk)
        try:
            files = await run_in_threadpool(services().runs.remote_results, run, Path(tmp))
        except ArchiveError as e:
            raise HTTPException(400, str(e)) from e
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"files": files}


@router.post("/runs/{run_id}/finish")
def finish(run_id: str, body: FinishRequest, name: str = Depends(runner_name)) -> dict:
    run = _claimed(run_id, name)
    services().runs.remote_finish(run, body.exit_code, body.error)
    services().audit.record(f"runner:{name}", "runner.finish", run_id, status=run.status)
    return {"status": run.status}
