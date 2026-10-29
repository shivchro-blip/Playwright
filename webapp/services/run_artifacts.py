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
class TestDetails:
    test_id: str
    browser: str
    status: str
    message: str = ""
    trace: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    log: str = ""  # the test's captured log (allure "log" attachment)

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
            source = str(att.get("source") or "")
            if not SOURCE_RE.match(source):
                continue
            path = results_dir / source
            if not path.is_file():
                continue
            kind = _kind(str(att.get("type") or ""), source)
            label = str(att.get("name") or kind)
            if kind == "text" and label == "log":
                details.log = _read_text(path, LOG_LIMIT, tail=True)
                continue
            item = Attachment(name=label, url=f"{self.runs_url}/{run_id}/allure-results/{source}", kind=kind,
                              size=path.stat().st_size)
            if kind == "text":
                item.text = _read_text(path, TEXT_LIMIT)
            details.attachments.append(item)
        return details
