"""REST API over the services (used by scripts/CI now, by a shared server later)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from webapp.services.json_files import ConflictError, PathError
from webapp.services.run_manager import RunConfig
from webapp.state import services

router = APIRouter(prefix="/api", tags=["automateny"])


class RunRequest(BaseModel):
    system: str
    env: str = ""
    tags: list[str]
    browsers: list[str] = Field(default_factory=lambda: ["chrome"])
    workers: int = Field(default=1, ge=1, le=16)
    options: dict[str, str | int | bool] = Field(default_factory=dict)
    test_ids: list[str] = Field(default_factory=list)


@router.get("/systems")
def list_systems() -> list[dict]:
    svc = services()
    with_tests = set(svc.catalog.systems_with_tests())
    return [{"name": s, "environments": svc.systems.environments(s), "has_tests": s in with_tests}
            for s in svc.systems.systems()]


@router.get("/tests")
def list_tests(system: str, tags: str = "") -> dict:
    catalog = services().catalog.load(system)
    selected = catalog.select(tags.split(",")) if tags else []
    return {
        "system": system,
        "tags": catalog.all_tags(),
        "tests": [asdict(t) for t in catalog.tests],
        "selected": [t.test_id for t in selected],
        "errors": catalog.errors,
    }


@router.get("/config")
def get_config() -> dict:
    env = services().env
    return {"path": str(env.env_path), "values": env.load(), "other": env.load_other()}


@router.put("/config")
def put_config(values: dict[str, str | int | bool]) -> dict:
    try:
        services().env.save(values)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return get_config()


@router.get("/credentials")
def credentials(system: str, env: str) -> list[dict]:
    status = services().credential_status(system, env)
    if status is None:
        raise HTTPException(404, f"No systemData entry for {system}/{env}")
    return status


# ───────────── generator: pages & test cases ─────────────

class SaveRequest(BaseModel):
    data: dict
    hash: str | None = None  # from the last GET; omit only when creating a new file


def _editor_call(fn, *args):
    try:
        return fn(*args)
    except PathError as e:
        raise HTTPException(400, str(e)) from e
    except ConflictError as e:
        raise HTTPException(409, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, "File not found") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/pages/{system}")
def list_pages(system: str) -> list[dict]:
    return [asdict(p) for p in _editor_call(services().pom.list_pages, system)]


@router.get("/pages/{system}/{rel:path}")
def get_page(system: str, rel: str) -> dict:
    data, sha = _editor_call(services().pom.load, system, rel)
    return {"data": data, "hash": sha}


@router.put("/pages/{system}/{rel:path}")
def put_page(system: str, rel: str, req: SaveRequest) -> dict:
    return {"hash": _editor_call(services().pom.save, system, rel, req.data, req.hash)}


@router.get("/testcases/{system}")
def list_testcases(system: str) -> dict:
    refs, errors = _editor_call(services().tests.list_tests, system)
    return {"tests": [asdict(r) for r in refs], "errors": errors}


@router.get("/testcases/{system}/{rel:path}")
def get_testcase_file(system: str, rel: str) -> dict:
    data, sha = _editor_call(services().tests.load_file, system, rel)
    return {"data": data, "hash": sha}


@router.put("/testcases/{system}/{rel:path}")
def put_testcase(system: str, rel: str, req: SaveRequest, index: int | None = None) -> dict:
    """Replace testCases[index] in the file (append when index is omitted)."""
    new_index, sha = _editor_call(services().tests.save, system, rel, index, req.data, req.hash)
    return {"index": new_index, "hash": sha}


@router.post("/testcases/{system}/validate")
def validate_testcase(system: str, data: dict) -> dict:
    return asdict(services().tests.validate(system, data))


@router.get("/runs")
def list_runs() -> list[dict]:
    return [{k: v for k, v in r.to_dict().items() if k != "results"} for r in services().runs.list()]


@router.post("/runs", status_code=201)
def start_run(req: RunRequest) -> dict:
    svc = services()
    selected = svc.catalog.load(req.system).select(req.tags)
    try:
        run = svc.runs.start(RunConfig(**req.model_dump()), expected=len(selected) * max(len(req.browsers), 1))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return run.to_dict()


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = services().runs.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run.to_dict()


@router.get("/runs/{run_id}/log")
def get_run_log(run_id: str, since: int = 0) -> dict:
    run = services().runs.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    lines, end = run.log_since(since)
    return {"lines": lines, "next": end, "status": run.status}


@router.post("/runs/{run_id}/rerun", status_code=201)
def rerun(run_id: str) -> dict:
    runs = services().runs
    if not runs.get(run_id):
        raise HTTPException(404, "Run not found")
    try:
        return runs.rerun(run_id).to_dict()
    except ValueError as e:  # stored config no longer valid (e.g. a removed run option)
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    if not services().runs.delete(run_id):
        raise HTTPException(409, "Run not found or still active")
    return {"deleted": run_id}


@router.get("/help")
def help_topics(q: str = "") -> list[dict]:
    svc = services().help
    if q:
        return [{**asdict(h.topic), "snippet": h.snippet} for h in svc.search(q)]
    return [asdict(t) for t in svc.topics()]


@router.get("/help/{topic_id}")
def help_topic(topic_id: str) -> dict:
    svc = services().help
    topic = svc.get(topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    return {**asdict(topic), "markdown": svc.markdown(topic)}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    if not services().runs.cancel(run_id):
        raise HTTPException(409, "Run is not active")
    return {"cancelled": run_id}
