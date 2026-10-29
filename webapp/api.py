"""REST API over the services (used by scripts/CI now, by a shared server later)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from webapp.services.json_files import ConflictError, PathError
from webapp.services.run_insights import compare, flaky_tests, previous_run, summarize
from webapp.services.run_manager import RunConfig
from webapp.services.schedules import validate as validate_schedule
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


@router.get("/runs/{run_id}/compare")
def compare_runs(run_id: str, base: str = "") -> dict:
    """What changed test by test from `base` (default: the previous finished run of the same system/env)."""
    runs = services().runs
    target = runs.get(run_id)
    if not target:
        raise HTTPException(404, "Run not found")
    before = runs.get(base) if base else previous_run(runs.list(), target)
    if not before:
        raise HTTPException(404, "No run to compare with")
    first, second = sorted((before, target), key=lambda r: r.created_at)
    rows = compare(first, second)
    return {"base": first.id, "target": second.id, "summary": summarize(rows), "tests": [r.to_dict() for r in rows]}


@router.post("/runs/{run_id}/report")
def write_run_report(run_id: str) -> dict:
    """(Re)write the built-in report; it is served at the returned url."""
    runs = services().runs
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.active or not runs.write_summary(run):
        raise HTTPException(409, "Run is still active or the report could not be written")
    return {"url": f"/runs/{run.id}/report.html"}


@router.get("/insights/flaky")
def list_flaky(system: str, env: str | None = None) -> list[dict]:
    """Tests that passed and failed in their recent runs, changing result at least twice."""
    return [t.to_dict() for t in flaky_tests(services().runs.list(), system, env)]


def _schedule_view(schedule) -> dict:
    scheduler = services().scheduler
    nxt = scheduler.next_run(schedule)
    last = scheduler.last_runs(schedule, 1)
    return {**schedule.to_dict(), "next_run": nxt.isoformat() if nxt else None,
            "last_run": {"id": last[0].id, "status": last[0].status} if last else None,
            "error": scheduler.errors.get(schedule.id, ""), "queued": schedule.id in scheduler.pending}


@router.get("/schedules")
def list_schedules() -> list[dict]:
    return [_schedule_view(s) for s in services().schedules.list()]


@router.post("/schedules", status_code=201)
def create_schedule(data: dict) -> dict:
    data = {k: v for k, v in data.items() if k != "id"}
    try:
        return _schedule_view(services().schedules.save(validate_schedule(data)))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except ConflictError as e:
        raise HTTPException(409, str(e)) from e


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, data: dict) -> dict:
    store = services().schedules
    if not store.get(schedule_id):
        raise HTTPException(404, "Schedule not found")
    try:
        return _schedule_view(store.save(validate_schedule({**data, "id": schedule_id})))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except ConflictError as e:
        raise HTTPException(409, str(e)) from e


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict:
    if not services().schedules.delete(schedule_id):
        raise HTTPException(404, "Schedule not found")
    return {"deleted": schedule_id}


@router.post("/schedules/{schedule_id}/run", status_code=201)
def run_schedule(schedule_id: str) -> dict:
    try:
        run = services().scheduler.run_now(schedule_id)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e
    return {"queued": True} if run is None else run.to_dict()


@router.post("/schedules/{schedule_id}/test-notification")
def test_notification(schedule_id: str) -> list[dict]:
    svc = services()
    schedule = svc.schedules.get(schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    return svc.scheduler.send_test(schedule)


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
