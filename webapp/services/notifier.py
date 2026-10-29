"""Run notifications: Slack, Microsoft Teams, a generic JSON webhook, and email.

Webhook URLs and SMTP passwords are credentials, so they live only in .env (or the environment) under
NOTIFY_* keys; schedules name the key, never the value, and this app never shows or writes them.

Email settings: NOTIFY_SMTP_HOST, NOTIFY_SMTP_PORT (587), NOTIFY_SMTP_USER, NOTIFY_SMTP_PASSWORD,
NOTIFY_SMTP_FROM, NOTIFY_SMTP_TLS (starttls | ssl | none; default starttls).
"""
from __future__ import annotations

import json
import logging
import re
import smtplib
import ssl
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import TYPE_CHECKING, Callable, Iterable
from urllib.parse import urlsplit

from webapp.services.run_insights import FAILING, compare, previous_run, summarize

if TYPE_CHECKING:
    from webapp.services.run_manager import Run

logger = logging.getLogger(__name__)

CHANNEL_TYPES = ("slack", "teams", "webhook", "email")
URL_ENV_RE = re.compile(r"^NOTIFY_[A-Z0-9_]{1,60}$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_FAILED = 10
TIMEOUT = 10
DEFAULT_ENV = {"slack": "NOTIFY_SLACK_URL", "teams": "NOTIFY_TEAMS_URL", "webhook": "NOTIFY_WEBHOOK_URL"}

Lookup = Callable[[str], "str | None"]  # env key -> value (os.environ, then .env)


class NotifyError(Exception):
    pass


def validate_channel(channel: dict) -> dict:
    """A normalized channel config, or ValueError."""
    kind = str(channel.get("type") or "")
    if kind not in CHANNEL_TYPES:
        raise ValueError(f"Unknown notification channel {kind!r}; use one of {', '.join(CHANNEL_TYPES)}.")
    if kind == "email":
        to = channel.get("to") or []
        to = [a.strip() for a in (to.split(",") if isinstance(to, str) else to) if str(a).strip()]
        bad = [a for a in to if not EMAIL_RE.match(a)]
        if not to or bad:
            raise ValueError("Email: enter one or more addresses" + (f" (not valid: {', '.join(bad)})" if bad else "."))
        return {"type": "email", "to": to}
    key = str(channel.get("url_env") or DEFAULT_ENV[kind])
    if not URL_ENV_RE.match(key):
        raise ValueError(f"{kind}: the URL must come from a .env key starting with NOTIFY_ (got {key!r}).")
    return {"type": kind, "url_env": key}


def channel_label(channel: dict) -> str:
    if channel["type"] == "email":
        return "Email to " + ", ".join(channel["to"])
    return {"slack": "Slack", "teams": "Teams", "webhook": "Webhook"}[channel["type"]] + f" ({channel['url_env']})"


def channel_ready(channel: dict, lookup: Lookup) -> bool:
    """Is the channel's secret configured? Never returns the value."""
    if channel["type"] == "email":
        return bool(lookup("NOTIFY_SMTP_HOST"))
    return bool(lookup(channel["url_env"]))


# ───────────── message ─────────────

@dataclass
class Message:
    title: str
    status: str
    lines: list[str]
    facts: list[tuple[str, str]]
    failed: list[tuple[str, str]]  # test, first line of the error
    url: str
    data: dict = field(default_factory=dict)  # the webhook payload

    @property
    def text(self) -> str:
        parts = [self.title, *self.lines]
        if self.failed:
            parts.append("Failed:")
            parts += [f"  - {t}: {m}" if m else f"  - {t}" for t, m in self.failed]
        if self.url:
            parts.append(f"Report: {self.url}")
        return "\n".join(parts)


def build_message(run: Run, runs: Iterable[Run], public_url: str, schedule_name: str = "") -> Message:
    runs = list(runs)
    cfg = run.config
    counts = run.counts()
    total = sum(counts.values())
    failed = sorted((r for r in run.results.values() if r.outcome in FAILING), key=lambda r: r.test_id)
    base = previous_run(runs, run)
    changes = summarize(compare(base, run)) if base else {}
    url = f"{public_url.rstrip('/')}/runs/{run.id}/report.html" if public_url else ""
    who = f"{schedule_name}: " if schedule_name else ""
    title = f"{who}{cfg.system}/{cfg.env} {run.status.upper()} ({total - len(failed)}/{total} passed)"
    lines = [f"Run {run.id}, tags {', '.join(cfg.tags)}, browsers {', '.join(cfg.browsers)}"]
    if run.error:
        lines.append(run.error)
    if changes.get("new failure"):
        lines.append(f"{changes['new failure']} new failure(s) since the previous run")
    if changes.get("fixed"):
        lines.append(f"{changes['fixed']} fixed since the previous run")
    first_lines = [(f"{r.test_id} [{r.browser}]" if r.browser else r.test_id,
                    (r.message or "").strip().splitlines()[0][:200] if r.message else "") for r in failed]
    shown = first_lines[:MAX_FAILED]
    if len(first_lines) > MAX_FAILED:
        shown.append((f"... and {len(first_lines) - MAX_FAILED} more", ""))
    facts = [("Status", run.status), ("System", f"{cfg.system}/{cfg.env}"), ("Tests", str(total)),
             *[(k.capitalize(), str(v)) for k, v in sorted(counts.items())]]
    data = {"event": "run.finished", "schedule": schedule_name, "run": {
        "id": run.id, "status": run.status, "system": cfg.system, "env": cfg.env, "tags": cfg.tags,
        "browsers": cfg.browsers, "counts": counts, "error": run.error, "started_at": run.started_at,
        "finished_at": run.finished_at, "report_url": url, "changes": changes,
        "failed": [{"test_id": r.test_id, "browser": r.browser, "message": (r.message or "")[:1000]} for r in failed],
    }}
    return Message(title, run.status, lines, facts, shown, url, data)


def _slack(msg: Message) -> dict:
    icon = ":white_check_mark:" if msg.status == "passed" else ":x:"
    return {"text": f"{icon} {msg.text}"}


def _teams(msg: Message) -> dict:
    body: list[dict] = [
        {"type": "TextBlock", "text": msg.title, "weight": "Bolder", "size": "Medium", "wrap": True,
         "color": "Good" if msg.status == "passed" else "Attention"},
        *[{"type": "TextBlock", "text": line, "wrap": True, "spacing": "Small"} for line in msg.lines],
        {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in msg.facts]},
    ]
    if msg.failed:
        body.append({"type": "TextBlock", "text": "Failed", "weight": "Bolder", "spacing": "Medium"})
        body += [{"type": "TextBlock", "text": f"{t}: {m}" if m else t, "wrap": True, "spacing": "None"}
                 for t, m in msg.failed]
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
            "version": "1.4", "body": body,
            "actions": [{"type": "Action.OpenUrl", "title": "Open report", "url": msg.url}] if msg.url else []}
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


# ───────────── delivery ─────────────

def _post(url: str, payload: dict) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise NotifyError("the configured URL is not an http(s) URL")
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "AutomateNY"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (scheme checked above)
            if not 200 <= response.status < 300:
                raise NotifyError(f"HTTP {response.status}")
    except OSError as e:  # URLError, HTTPError, timeouts
        # Never echo the URL: it is a credential
        reason = getattr(e, "code", None) or getattr(e, "reason", None) or type(e).__name__
        raise NotifyError(f"delivery failed ({reason})") from None


def _email(msg: Message, to: list[str], lookup: Lookup) -> None:
    host = lookup("NOTIFY_SMTP_HOST")
    if not host:
        raise NotifyError("NOTIFY_SMTP_HOST is not set in .env")
    port = int(lookup("NOTIFY_SMTP_PORT") or 587)
    mode = (lookup("NOTIFY_SMTP_TLS") or "starttls").strip().lower()
    user, password = lookup("NOTIFY_SMTP_USER"), lookup("NOTIFY_SMTP_PASSWORD")
    email = EmailMessage()
    email["Subject"] = f"[AutomateNY] {msg.title}"
    email["From"] = lookup("NOTIFY_SMTP_FROM") or user or f"automateny@{host}"
    email["To"] = ", ".join(to)
    email.set_content(msg.text)
    context = ssl.create_default_context()
    try:
        if mode == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        with server:
            if mode == "starttls":
                server.starttls(context=context)
            if user and password:
                server.login(user, password)
            server.send_message(email)
    except (OSError, smtplib.SMTPException) as e:
        raise NotifyError(f"email failed ({type(e).__name__}: {str(e)[:200]})") from None


class Notifier:
    def __init__(self, lookup: Lookup, public_url: str = ""):
        self.lookup = lookup
        self.public_url = public_url

    def send(self, msg: Message, channels: list[dict]) -> list[dict]:
        """Deliver to every channel; one failure doesn't stop the others. [{channel, ok, error}]"""
        results = []
        for channel in channels:
            label = channel_label(channel)
            try:
                if channel["type"] == "email":
                    _email(msg, channel["to"], self.lookup)
                else:
                    url = self.lookup(channel["url_env"])
                    if not url:
                        raise NotifyError(f"{channel['url_env']} is not set in .env")
                    payload = {"slack": _slack, "teams": _teams}.get(channel["type"], lambda m: m.data)(msg)
                    _post(url.strip(), payload)
                results.append({"channel": label, "ok": True, "error": ""})
            except NotifyError as e:
                logger.warning("Notification to %s failed: %s", label, e)
                results.append({"channel": label, "ok": False, "error": str(e)})
        return results

    def test_message(self, schedule_name: str) -> Message:
        return Message(title=f"{schedule_name}: test notification from AutomateNY", status="passed",
                       lines=["Notifications for this schedule are set up correctly."],
                       facts=[("Schedule", schedule_name)], failed=[], url=self.public_url,
                       data={"event": "test", "schedule": schedule_name})
