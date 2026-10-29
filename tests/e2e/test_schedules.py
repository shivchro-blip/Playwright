"""A schedule fires a real run and its failure reaches a webhook (local HTTP server, no internet)."""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.e2e.test_runs import REPO_ROOT
from webapp.services.notifier import Notifier
from webapp.services.run_manager import RunManager
from webapp.services.schedules import Scheduler, ScheduleStore, read_notifications, validate
from webapp.services.test_catalog import TestCatalogService as Catalog  # pytest collects Test* names


def test_scheduled_failure_is_notified(workspace: Path, tmp_path: Path):
    received: list[dict] = []

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runs = RunManager(repo_root=REPO_ROOT, workspace=workspace, runs_dir=tmp_path / "runs",
                          python_exe=sys.executable, allure_cmd="allure-not-installed")
        store = ScheduleStore(workspace / "Data" / "schedules.json")
        env = {"NOTIFY_WEBHOOK_URL": f"http://127.0.0.1:{server.server_address[1]}/hook"}
        scheduler = Scheduler(store, runs, Catalog(workspace), Notifier(env.get, "http://app.test"),
                              clock=lambda: datetime(2026, 10, 30, 6, 30))
        schedule = store.save(validate({"name": "Nightly bad autoGen", "cron": "30 6 * * 1-5", "system": "SMOKE",
                                        "env": "dev", "tags": ["fail"], "options": {"headlessMode": True},
                                        "channels": [{"type": "webhook"}]}))
        [run] = scheduler.tick()
        assert run.trigger == f"schedule:{schedule.id}" and scheduler.tick() == []
        deadline = time.monotonic() + 240
        while (run.active or not received) and time.monotonic() < deadline:
            time.sleep(0.5)
    finally:
        server.shutdown()
    assert run.status == "failed", "\n".join(run.log[-60:])
    assert received and received[0]["schedule"] == "Nightly bad autoGen"
    payload = received[0]["run"]
    assert payload["id"] == run.id and payload["failed"][0]["test_id"] == "TC-SMOKE-003-BadAutoGen"
    assert payload["report_url"] == f"http://app.test/runs/{run.id}/report.html"
    assert read_notifications(run) == [{"channel": "Webhook (NOTIFY_WEBHOOK_URL)", "ok": True, "error": ""}]
    reloaded = RunManager(repo_root=REPO_ROOT, workspace=workspace, runs_dir=tmp_path / "runs",
                          python_exe=sys.executable).get(run.id)
    assert reloaded.trigger == run.trigger  # survives an app restart (run.json)
