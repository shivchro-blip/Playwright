"""The event bus, and the places that publish on it."""
import json
import threading
import time

from webapp.services import json_files
from webapp.services.events import EventBus, publish_on
from webapp.services.run_manager import Run, RunConfig, RunManager
from webapp.state import workspace_writes


def test_publish_reaches_the_topics_subscribed():
    bus, heard, every = EventBus(), [], []
    bus.subscribe(heard.append, ["run"])
    bus.subscribe(every.append)
    bus.publish("run", id="r1", change="saved")
    bus.publish("workspace", path="Pages/A.json")
    assert [e["id"] for e in heard] == ["r1"]
    assert [e["topic"] for e in every] == ["run", "workspace"]
    assert [e["seq"] for e in every] == [1, 2]


def test_unsubscribe_and_a_broken_subscriber():
    bus, heard = EventBus(), []

    def broken(event):
        raise RuntimeError("boom")
    bus.subscribe(broken)
    stop = bus.subscribe(heard.append)
    assert bus.subscribers == 2
    bus.publish("run", id="a")  # the broken one is logged, the other still hears
    stop()
    bus.publish("run", id="b")
    assert [e["id"] for e in heard] == ["a"] and bus.subscribers == 1


def test_versions_since_and_kept():
    bus = EventBus(kept=3)
    assert bus.version() == 0 and bus.version("run") == 0
    for n in range(5):
        bus.publish("run" if n % 2 == 0 else "workspace", n=n)
    assert bus.version("run") == 5 and bus.version("workspace") == 4 and bus.version() == 5
    assert [e["seq"] for e in bus.since(0)] == [3, 4, 5]  # only the last 3 are kept
    assert [e["seq"] for e in bus.since(3, ["run"])] == [5]
    assert bus.since(5) == []


def test_wait_wakes_on_publish_and_times_out():
    bus = EventBus()
    assert bus.wait(0, 0.05) is False
    threading.Timer(0.05, lambda: bus.publish("run")).start()
    started = time.monotonic()
    assert bus.wait(0, 2.0) is True and time.monotonic() - started < 1.5


def test_publish_on_owners_without_a_bus():
    class Fake:
        pass
    publish_on(Fake(), "monitor", id="m")  # nothing to publish on: no error
    owner = Fake()
    owner.bus = EventBus()
    publish_on(owner, "monitor", id="m")
    assert owner.bus.since(0)[0]["id"] == "m"


def test_workspace_writes_are_published_by_path(tmp_path):
    bus = EventBus()
    listener = workspace_writes(bus, tmp_path)
    json_files.WRITE_LISTENERS.append(listener)
    try:
        json_files.write_json(tmp_path / "Pages" / "SYS" / "Home.json", {"a": 1}, None)
        json_files.write_json(tmp_path.parent / "elsewhere.json", {"b": 2}, None)  # outside the workspace: not told
    finally:
        json_files.WRITE_LISTENERS.remove(listener)
    [event] = bus.since(0)
    assert (event["topic"], event["path"], event["folder"]) == ("workspace", "Pages/SYS/Home.json", "Pages")


def test_run_manager_publishes_saves_steps_results_and_deletes(tmp_path):
    runs_dir = tmp_path / "runs"
    manager = RunManager(repo_root=tmp_path, workspace=tmp_path, runs_dir=runs_dir, python_exe="python")
    heard = []
    manager.bus.subscribe(heard.append, ["run"])
    run = Run(id="r1", config=RunConfig(system="S", env="qa", tags=[]), dir=runs_dir / "r1", status="running",
              trigger="schedule:s1")
    run.dir.mkdir(parents=True)
    manager._runs[run.id] = run
    manager._save(run)
    manager._handle_event(run, json.dumps({"event": "start", "nodeid": "t.py::test[chrome-TC-1]"}))
    run.results_dir.mkdir(parents=True, exist_ok=True)
    (run.results_dir / "autoplay-steps-1.jsonl").write_text(json.dumps(
        {"event": "step", "phase": "start", "nodeid": "t.py::test[chrome-TC-1]", "n": 1, "of": 2,
         "kind": "page", "label": "Home"}) + "\n", encoding="utf-8")
    manager.read_steps(run)
    manager.read_steps(run)  # nothing new: nothing told
    for line in ("one", "two", "three"):
        manager._append(run, line)  # console text is told at most every LOG_PUBLISH_SECONDS
    run.status = "passed"
    assert manager.delete("r1")
    changes = [e["change"] for e in heard]
    assert changes[:3] == ["saved", "result", "step"] and changes.count("step") == 1
    assert changes.count("log") == 1 and changes[-1] == "deleted"
    assert heard[0]["trigger"] == "schedule:s1" and heard[0]["system"] == "S"
