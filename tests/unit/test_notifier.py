"""Notifications: message content, Slack/Teams/webhook payloads, delivery, email, secrets never echoed."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from webapp.services import notifier as nt
from webapp.services.notifier import Notifier, build_message, channel_ready, validate_channel
from webapp.services.run_manager import Run, RunConfig
from webapp.services.run_manager import TestResult as Result  # pytest collects Test* names
from webapp.state import notify_lookup


def run_with(tmp_path, *outcomes, n=1, created="2026-10-30T06:30:00"):
    run = Run(id=f"run-{n}", config=RunConfig(system="SYS", env="dev", tags=["smoke"], browsers=["chromium"]),
              dir=tmp_path, status="failed" if "failed" in outcomes else "passed", created_at=created)
    for i, o in enumerate(outcomes):
        run.results[str(i)] = Result(nodeid=str(i), test_id=f"TC-{i}", browser="chromium", outcome=o,
                                     message="Timeout 5000ms\nCall log: ..." if o == "failed" else "")
    return run


def test_message_lists_failures_changes_and_the_report(tmp_path):
    before = run_with(tmp_path, "passed", "passed", n=1, created="2026-10-29T06:30:00")
    after = run_with(tmp_path, "failed", "passed", n=2)
    msg = build_message(after, [before, after], "http://app.test:8080/", "Nightly")
    assert msg.title == "Nightly: SYS/dev FAILED (1/2 passed)"
    assert msg.failed == [("TC-0 [chromium]", "Timeout 5000ms")]
    assert "1 new failure(s) since the previous run" in msg.lines
    assert msg.url == "http://app.test:8080/runs/run-2/report.html" and msg.url in msg.text
    assert msg.data["run"]["failed"][0]["message"].startswith("Timeout") and msg.data["event"] == "run.finished"
    slack, teams = nt._slack(msg), nt._teams(msg)
    assert slack["text"].startswith(":x: Nightly")
    card = teams["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["actions"][0]["url"] == msg.url
    many = run_with(tmp_path, *["failed"] * 13, n=3)
    assert len(build_message(many, [many], "").failed) == 11  # 10 + "... and 3 more"


def test_channel_validation_and_readiness():
    assert validate_channel({"type": "teams"}) == {"type": "teams", "url_env": "NOTIFY_TEAMS_URL"}
    with pytest.raises(ValueError):
        validate_channel({"type": "webhook", "url_env": "AWS_SECRET_ACCESS_KEY"})
    env = {"NOTIFY_SLACK_URL": "https://hooks.test/x"}
    assert channel_ready({"type": "slack", "url_env": "NOTIFY_SLACK_URL"}, env.get)
    assert not channel_ready({"type": "email"}, env.get)


def test_lookup_only_reads_notify_keys(tmp_path, monkeypatch):
    from webapp.services.env_config import EnvConfigService
    dotenv = tmp_path / ".env"
    dotenv.write_text("NOTIFY_WEBHOOK_URL=https://hook.test/a\nAPP_PASSWORD=hunter2\n", encoding="utf-8")
    lookup = notify_lookup(EnvConfigService(dotenv))
    assert lookup("NOTIFY_WEBHOOK_URL") == "https://hook.test/a" and lookup("APP_PASSWORD") is None
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://env.test/b")
    assert lookup("NOTIFY_WEBHOOK_URL") == "https://env.test/b"


@pytest.fixture
def hook_server():
    received, status = [], {"code": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(status["code"])
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/hook/SECRET-TOKEN", received, status
    server.shutdown()


def test_delivery_reports_each_channel_without_leaking_the_url(tmp_path, hook_server):
    url, received, status = hook_server
    env = {"NOTIFY_WEBHOOK_URL": url, "NOTIFY_SLACK_URL": url, "NOTIFY_TEAMS_URL": "ftp://nope"}
    notifier = Notifier(env.get, "http://app.test")
    msg = build_message(run_with(tmp_path, "failed"), [], notifier.public_url, "Nightly")
    channels = [validate_channel({"type": t}) for t in ("webhook", "slack", "teams")]
    channels.append(validate_channel({"type": "webhook", "url_env": "NOTIFY_MISSING"}))
    results = notifier.send(msg, channels)
    assert [r["ok"] for r in results] == [True, True, False, False]
    assert received[0]["run"]["status"] == "failed" and received[1]["text"].startswith(":x:")
    assert "not set" in results[3]["error"]
    status["code"] = 500
    failed = notifier.send(msg, channels[:1])[0]
    assert not failed["ok"] and "500" in failed["error"] and "SECRET-TOKEN" not in failed["error"]


def test_email_uses_smtp_settings(tmp_path, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.update(host=host, port=port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            sent["tls"] = True

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(nt.smtplib, "SMTP", FakeSMTP)
    env = {"NOTIFY_SMTP_HOST": "smtp.test", "NOTIFY_SMTP_USER": "bot@x.test", "NOTIFY_SMTP_PASSWORD": "pw"}
    notifier = Notifier(env.get, "")
    msg = build_message(run_with(tmp_path, "failed"), [], "", "Nightly")
    result = notifier.send(msg, [validate_channel({"type": "email", "to": "qa@x.test"})])
    assert result[0]["ok"], result
    assert sent["host"] == "smtp.test" and sent["port"] == 587 and sent["tls"] and sent["login"] == "bot@x.test"
    assert sent["message"]["To"] == "qa@x.test" and "TC-0" in sent["message"].get_content()
    assert not Notifier({}.get).send(msg, [validate_channel({"type": "email", "to": "qa@x.test"})])[0]["ok"]


def test_notify_keys_are_secrets_for_the_config_api(tmp_path):
    from webapp.services.env_config import EnvConfigService, is_secret
    dotenv = tmp_path / ".env"
    dotenv.write_text("NOTIFY_SLACK_URL=https://hooks.slack.com/services/T/B/x\nOTHER=1\n", encoding="utf-8")
    env = EnvConfigService(dotenv)
    assert is_secret("NOTIFY_SLACK_URL") and env.load_other() == {"OTHER": "1"}
    with pytest.raises(ValueError):
        env.save({"NOTIFY_SLACK_URL": "https://evil.test"})
