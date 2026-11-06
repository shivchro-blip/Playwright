"""REST API over the services (used by scripts/CI now, by a shared server later)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from webapp.services.json_files import ConflictError, PathError
from webapp.services.run_insights import compare, flaky_tests, previous_run, summarize
from webapp.services.run_manager import RunConfig
from webapp.services.schedules import validate as validate_schedule
from webapp.auth import require
from webapp.state import services

router = APIRouter(prefix="/api", tags=["autoplay"], dependencies=[require("view")])


class RunRequest(BaseModel):
    system: str
    env: str = ""
    tags: list[str]
    browsers: list[str] = Field(default_factory=lambda: ["chrome"])
    workers: int = Field(default=1, ge=1, le=16)
    options: dict[str, str | int | bool] = Field(default_factory=dict)
    test_ids: list[str] = Field(default_factory=list)
    runner: str = ""  # a registered remote runner; "" runs on the machine hosting the app
    also_envs: list[str] = Field(default_factory=list)  # run the same selection on these environments, paired


@router.get("/systems")
def list_systems() -> list[dict]:
    svc = services()
    with_tests = set(svc.catalog.systems_with_tests())
    return [{"name": s, "environments": svc.systems.environments(s), "has_tests": s in with_tests}
            for s in svc.systems.systems()]


@router.get("/runners")
def list_runners() -> list[dict]:
    return services().runners.list()


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


@router.put("/config", dependencies=[require("settings")])
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


@router.put("/pages/{system}/{rel:path}", dependencies=[require("edit")])
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


@router.put("/testcases/{system}/{rel:path}", dependencies=[require("edit")])
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


@router.post("/runs", status_code=201, dependencies=[require("run")])
def start_run(req: RunRequest) -> dict:
    svc = services()
    selected = svc.catalog.load(req.system).select(req.tags)
    known = svc.systems.environments(req.system)
    unknown = [e for e in req.also_envs if e not in known]
    if unknown:
        raise HTTPException(400, f"Unknown environment for {req.system}: {', '.join(unknown)}")
    credentials, _, _ = svc.prepare_credentials(req.system, [req.env, *req.also_envs], use_vault=not req.runner)
    try:
        runs = svc.runs.start_compared(RunConfig(**req.model_dump(exclude={"also_envs"})), req.also_envs,
                                       expected=len(selected) * max(len(req.browsers), 1), credentials=credentials)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:  # the queue is full
        raise HTTPException(409, str(e)) from e
    run = runs[0]
    return {**run.to_dict(), "queue_position": svc.runs.queue_position(run.id),
            "compare_runs": [r.id for r in runs[1:]]}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    runs = services().runs
    run = runs.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {**run.to_dict(), "queue_position": runs.queue_position(run.id)}


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


@router.post("/runs/{run_id}/report", dependencies=[require("run")])
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
            "error": scheduler.errors.get(schedule.id, ""), "queued": scheduler.queued(schedule)}


@router.get("/schedules")
def list_schedules() -> list[dict]:
    return [_schedule_view(s) for s in services().schedules.list()]


@router.post("/schedules", status_code=201, dependencies=[require("schedule")])
def create_schedule(data: dict) -> dict:
    data = {k: v for k, v in data.items() if k != "id"}
    try:
        return _schedule_view(services().schedules.save(validate_schedule(data)))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except ConflictError as e:
        raise HTTPException(409, str(e)) from e


@router.put("/schedules/{schedule_id}", dependencies=[require("schedule")])
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


@router.delete("/schedules/{schedule_id}", dependencies=[require("schedule")])
def delete_schedule(schedule_id: str) -> dict:
    if not services().schedules.delete(schedule_id):
        raise HTTPException(404, "Schedule not found")
    return {"deleted": schedule_id}


@router.post("/schedules/{schedule_id}/run", status_code=201, dependencies=[require("run")])
def run_schedule(schedule_id: str) -> dict:
    try:
        run = services().scheduler.run_now(schedule_id)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e
    return run.to_dict()


@router.post("/schedules/{schedule_id}/test-notification", dependencies=[require("schedule")])
def test_notification(schedule_id: str) -> list[dict]:
    svc = services()
    schedule = svc.schedules.get(schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    return svc.scheduler.send_test(schedule)


@router.post("/runs/{run_id}/rerun", status_code=201, dependencies=[require("run")])
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


@router.delete("/runs/{run_id}", dependencies=[require("delete")])
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


@router.get("/journeys")
def list_journeys(system: str) -> list[dict]:
    """Each journey of a system with its status on every environment and its coverage."""
    out = []
    for o in services().journeys.overview(system):
        out.append({**o.journey.to_dict(), "worst": o.worst,
                    "statuses": {env: {"state": st.state, "label": st.label, "last_run": st.last_run,
                                       "last_at": st.last_at, "pass_rate": st.pass_rate, "where": st.where,
                                       "tests": [asdict(t) for t in st.tests]} for env, st in o.statuses.items()},
                    "coverage": {"percent": o.coverage.percent, "missing_tests": o.coverage.missing_tests,
                                 "screens": [{"screen": c.screen, "used": c.used, "total": c.total}
                                             for c in o.coverage.screens]}})
    return out


@router.post("/journeys/{journey_id}/run", status_code=201, dependencies=[require("run")])
def run_journey(journey_id: str, env: str) -> dict:
    from webapp.services.journeys import JourneyError
    try:
        run = services().start_journey(journey_id, env)
    except JourneyError as e:
        raise HTTPException(400, str(e)) from e
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e
    return run.to_dict()


def _monitor_view(monitor) -> dict:
    return services().monitors.summary(monitor)


@router.get("/monitors")
def list_monitors() -> list[dict]:
    """Every monitor with its journeys' state, uptime over 24h/7d/30d, and recent incidents."""
    svc = services()
    return [_monitor_view(m) for m in svc.monitors.store.list()]


@router.get("/monitors/{monitor_id}")
def get_monitor(monitor_id: str) -> dict:
    monitor = services().monitors.store.get(monitor_id)
    if monitor is None:
        raise HTTPException(404, "Monitor not found")
    return _monitor_view(monitor)


def _save_monitor(data: dict, replaces: str | None, user) -> dict:
    from webapp.services.monitors import MonitorError, validate as validate_monitor
    svc = services()
    try:
        monitor = svc.monitors.store.save(validate_monitor(data, svc.journeys.store), replaces=replaces)
    except MonitorError as e:
        raise HTTPException(400, str(e)) from e
    svc.audit.record(user.username, "monitor.save", monitor.id, via="api")
    return _monitor_view(monitor)


@router.post("/monitors", status_code=201)
def create_monitor(data: dict, user=require("schedule")) -> dict:
    return _save_monitor({k: v for k, v in data.items() if k != "id"}, None, user)


@router.put("/monitors/{monitor_id}")
def update_monitor(monitor_id: str, data: dict, user=require("schedule")) -> dict:
    if services().monitors.store.get(monitor_id) is None:
        raise HTTPException(404, "Monitor not found")
    return _save_monitor({**data, "id": data.get("id") or monitor_id}, monitor_id, user)


@router.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: str, user=require("schedule")) -> dict:
    svc = services()
    if not svc.monitors.store.delete(monitor_id):
        raise HTTPException(404, "Monitor not found")
    svc.audit.record(user.username, "monitor.delete", monitor_id, via="api")
    return {"deleted": monitor_id}


@router.post("/monitors/{monitor_id}/check", status_code=201, dependencies=[require("run")])
def check_monitor(monitor_id: str) -> dict:
    """Run a check now. 409 while the previous check is still running or when it cannot start."""
    from webapp.services.monitors import MonitorError
    svc = services()
    try:
        run = svc.monitors.check_now(monitor_id)
    except MonitorError as e:
        raise HTTPException(404, str(e)) from e
    if run is None:
        raise HTTPException(409, svc.monitors.errors.get(monitor_id) or "The previous check is still running.")
    return run.to_dict()


@router.get("/summary")
def instance_summary() -> dict:
    """This instance's health in one call (for the control plane): journeys per system and environment, the
    failing critical and high journeys, the latest releases, monitors, runs and runners."""
    from webapp.services.summary import build_summary
    return build_summary(services())


class ImpactRequest(BaseModel):
    base: str = ""  # commit, tag or branch to compare with
    head: str = ""  # empty: the workspace files as they are now
    screens: dict[str, list[str]] = {}  # system -> screens the application changed
    env: str = ""  # for /impact/run


def _impact(req: ImpactRequest):
    from webapp.services.impact import ImpactError
    try:
        return services().impact.report(req.base.strip(), req.head.strip(), req.screens)
    except ImpactError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/impact")
def change_impact(req: ImpactRequest) -> dict:
    """What a change reaches: the test cases (and why), the journeys, and the changes no test case exercises."""
    return _impact(req).to_dict()


@router.post("/impact/run", status_code=201)
def run_impact(req: ImpactRequest, user=require("run")) -> dict:
    """Run the test cases a change reaches on an environment (one run per system)."""
    from webapp.services.impact import ImpactError
    from webapp.services.journeys import JourneyError
    svc = services()
    report = _impact(req)
    if not req.env:
        raise HTTPException(400, "Say which environment to run on (env).")
    try:
        runs = svc.impact.run(report, req.env)
    except (ImpactError, JourneyError) as e:
        raise HTTPException(400, str(e)) from e
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e
    svc.audit.record(user.username, "impact.run", ",".join(r.id for r in runs), env=req.env, via="api",
                     tests=len(report.tests))
    return {"runs": [r.to_dict() for r in runs], **report.to_dict()}


class ReleaseRequest(BaseModel):
    name: str
    system: str
    env: str
    journeys: list[str] | None = None  # ids; all of the system's when left out
    block: list[str] = Field(default_factory=lambda: ["critical", "high"])
    max_age_days: int = 7
    since: str = ""


@router.post("/releases", status_code=201)
def create_release(req: ReleaseRequest, user=require("run")) -> dict:
    """Check a release: the decision ("go" / "no-go") with its reasons. A CI pipeline can stop on "no-go"."""
    from webapp.services.releases import Policy, ReleaseError
    svc = services()
    try:
        policy = Policy.from_dict({"block": req.block, "max_age_days": req.max_age_days, "since": req.since})
        pack = svc.releases.create(req.name, req.system, req.env, req.journeys, policy, user.username)
    except ReleaseError as e:
        raise HTTPException(400, str(e)) from e
    svc.audit.record(user.username, "release.check", pack["id"], decision=pack["decision"], via="api")
    return {**svc.releases.summary(pack), "report": f"/api/releases/{pack['id']}/report"}


@router.get("/releases")
def list_releases(system: str = "") -> list[dict]:
    svc = services()
    return [svc.releases.summary(p) for p in svc.releases.store.list(system or None)]


@router.get("/releases/{release_id}")
def get_release(release_id: str) -> dict:
    svc = services()
    pack = svc.releases.store.get(release_id)
    if pack is None:
        raise HTTPException(404, "No such release check")
    return {**pack, "signoffs": svc.releases.store.signoffs(release_id), **svc.releases.store.verify(pack)}


@router.get("/releases/{release_id}/report", include_in_schema=False)
def release_report(release_id: str):
    from fastapi.responses import HTMLResponse
    svc = services()
    pack = svc.releases.store.get(release_id)
    if pack is None:
        raise HTTPException(404, "No such release check")
    return HTMLResponse(svc.releases.report_html(pack), headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff"})


class SignoffRequest(BaseModel):
    decision: str
    comment: str = ""


@router.post("/releases/{release_id}/signoff")
def signoff_release(release_id: str, req: SignoffRequest, user=require("signoff")) -> dict:
    from webapp.services.releases import ReleaseError
    svc = services()
    try:
        entry = svc.releases.store.add_signoff(release_id, user.username, user.role, req.decision, req.comment)
    except ReleaseError as e:
        raise HTTPException(400, str(e)) from e
    svc.audit.record(user.username, "release.signoff", release_id, decision=req.decision, via="api")
    return entry


@router.post("/runs/{run_id}/cancel", dependencies=[require("run")])
def cancel_run(run_id: str) -> dict:
    if not services().runs.cancel(run_id):
        raise HTTPException(409, "Run is not active")
    return {"cancelled": run_id}
