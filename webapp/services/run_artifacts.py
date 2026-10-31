"""Per-test failure details from a run's raw Allure results (no Allure CLI needed).

allure-pytest writes one <uuid>-result.json per test, named "<testCaseID> [<browser>]", with the error
(statusDetails) and its attachments (screenshot, DOM snapshot, action JSON, trace, video, captured log),
possibly nested inside steps. The files are served by the web app under /runs/<run id>/allure-results/.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

RESULT_NAME_RE = re.compile(r"^(?P<test_id>.*) \[(?P<browser>[^\]]+)\]$")
# Attachment file names allure-pytest generates; anything else in a result file is ignored (never served)
SOURCE_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}-attachment(\.[A-Za-z0-9]{1,8})?$")
FAILED = ("failed", "broken")
SCREEN_RE = re.compile(r"^-\s*(?:\[LOGIN\]\s*)?Screen:\s*(?P<name>.+?)\s*-$")
COMMON_RE = re.compile(r"^-\s*Common Action:\s*(?P<name>.+?)\s*-$")
REUSE_RE = re.compile(r"^\*\*\s*Reusing Test Case:\s*(?P<name>.+?)\s*\*\*$")
ACTION_RE = re.compile(r"^(?P<n>\d+)\)\s+(?P<action>\S+)\s+-\s+(?P<field>.+)$")
TEXT_LIMIT = 64 * 1024
LOG_LIMIT = 256 * 1024


@dataclass
class Attachment:
    name: str
    url: str
    kind: str  # image | html | trace | video | text | other
    size: int = 0
    text: str | None = None  # small text attachments, read inline


@dataclass
class FailurePoint:
    """Where in the test the first failure happened, read from the failing chain of Allure steps."""
    common_action: str = ""
    reused_test: str = ""
    screen: str = ""
    step_no: int | None = None  # the action's number within its screen
    action: str = ""  # FILL, CLICK, ...
    field_name: str = ""
    attachments: list[Attachment] = field(default_factory=list)  # screenshot, DOM snapshot, action JSON

    @property
    def crumbs(self) -> list[str]:
        out = []
        if self.reused_test:
            out.append(f"Reused test {self.reused_test}")
        if self.common_action:
            out.append(f"Common action {self.common_action}")
        if self.screen:
            out.append(f"Screen {self.screen}")
        if self.field_name:
            out.append(f"{self.action.upper()} {self.field_name}".strip() + (f" (action {self.step_no})"
                                                                         if self.step_no else ""))
        return out

    @property
    def short(self) -> str:
        where = f"{self.field_name} ({self.action})" if self.field_name else ""
        return " > ".join(c for c in (self.screen, where) if c)

    @property
    def screenshot(self) -> Attachment | None:
        return next((a for a in self.attachments if a.kind == "image"), None)


@dataclass
class TestDetails:
    test_id: str
    browser: str
    status: str
    message: str = ""
    trace: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    log: str = ""  # the test's captured log (allure "log" attachment)
    failure: FailurePoint | None = None

    def by_kind(self, kind: str) -> list[Attachment]:
        return [a for a in self.attachments if a.kind == kind]

    @property
    def has_artifacts(self) -> bool:
        return bool(self.message or any(a.kind != "text" for a in self.attachments))


def _kind(mime: str, source: str) -> str:
    mime = (mime or "").lower()
    if mime.startswith("image/"):
        return "image"
    if mime == "text/html" or source.endswith(".html"):
        return "html"
    if mime in ("application/zip", "application/x-zip-compressed") or source.endswith(".zip"):
        return "trace"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("text/") or mime in ("application/json",):
        return "text"
    return "other"


def _read_text(path: Path, limit: int, tail: bool = False) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[-limit:] if tail else data[:limit]
    return data.decode("utf-8", errors="replace")


def _failed_chain(node: dict) -> list[dict]:
    """Outermost-first chain of failed steps: follow the first failed child at every level."""
    for step in node.get("steps") or []:
        if isinstance(step, dict) and step.get("status") in FAILED:
            return [step] + _failed_chain(step)
    return []


def _walk_attachments(node: dict) -> list[dict]:
    found = list(node.get("attachments") or [])
    for step in node.get("steps") or []:
        if isinstance(step, dict):
            found += _walk_attachments(step)
    return found


class RunArtifacts:
    """Cached reader: re-parses a run's results only when files were added or changed."""

    def __init__(self, runs_url: str = "/runs"):
        self.runs_url = runs_url.rstrip("/")
        self._cache: dict[Path, tuple[tuple, dict]] = {}

    def details(self, run_id: str, results_dir: Path) -> dict[tuple[str, str], TestDetails]:
        """(test_id, browser) -> TestDetails for every finished test of the run."""
        results_dir = Path(results_dir)
        try:
            files = sorted(results_dir.glob("*-result.json"))
            stamp = tuple((f.name, f.stat().st_mtime_ns) for f in files)
        except OSError:
            return {}
        cached = self._cache.get(results_dir)
        if cached and cached[0] == stamp:
            return cached[1]
        out: dict[tuple[str, str], TestDetails] = {}
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("Unreadable Allure result: %s", f)
                continue
            details = self._parse(run_id, results_dir, data)
            if details:
                out[(details.test_id, details.browser)] = details
        self._cache[results_dir] = (stamp, out)
        return out

    def _parse(self, run_id: str, results_dir: Path, data: dict) -> TestDetails | None:
        name = str(data.get("name") or "")
        m = RESULT_NAME_RE.match(name)
        test_id, browser = (m.group("test_id"), m.group("browser")) if m else (name, "")
        status_details = data.get("statusDetails") or {}
        details = TestDetails(test_id=test_id, browser=browser, status=str(data.get("status") or ""),
                              message=str(status_details.get("message") or "").strip(),
                              trace=str(status_details.get("trace") or "").strip())
        for att in _walk_attachments(data):
            item = self._attachment(run_id, results_dir, att)
            if item == "log":
                details.log = self._log(results_dir, att)
            elif item:
                details.attachments.append(item)
        details.failure = self._failure(run_id, results_dir, data)
        return details

    def _attachment(self, run_id: str, results_dir: Path, att: dict):
        """An Attachment, the string "log" for the captured log, or None for anything not served."""
        source = str(att.get("source") or "")
        if not SOURCE_RE.match(source):
            return None
        path = results_dir / source
        if not path.is_file():
            return None
        kind = _kind(str(att.get("type") or ""), source)
        label = str(att.get("name") or kind)
        if kind == "text" and label == "log":
            return "log"
        item = Attachment(name=label, url=f"{self.runs_url}/{run_id}/allure-results/{source}", kind=kind,
                          size=path.stat().st_size)
        if kind == "text":
            item.text = _read_text(path, TEXT_LIMIT)
        return item

    @staticmethod
    def _log(results_dir: Path, att: dict) -> str:
        return _read_text(results_dir / str(att.get("source") or ""), LOG_LIMIT, tail=True)

    def _failure(self, run_id: str, results_dir: Path, data: dict) -> FailurePoint | None:
        chain = _failed_chain(data)
        if not chain:
            return None
        point = FailurePoint()
        action_step = None
        for step in chain:
            name = str(step.get("name") or "")
            if m := REUSE_RE.match(name):
                point.reused_test = point.reused_test or m.group("name")
            elif m := COMMON_RE.match(name):
                point.common_action = point.common_action or m.group("name")
            elif m := SCREEN_RE.match(name):
                point.screen = m.group("name")  # the innermost screen is where it failed
            elif (m := ACTION_RE.match(name)) and action_step is None:
                point.step_no, point.action, point.field_name = int(m.group("n")), m.group("action"), m.group("field")
                action_step = step
        if not (point.screen or point.field_name):
            return None
        for att in _walk_attachments(action_step or chain[-1]):
            item = self._attachment(run_id, results_dir, att)
            if item and item != "log":
                point.attachments.append(item)
        return point
