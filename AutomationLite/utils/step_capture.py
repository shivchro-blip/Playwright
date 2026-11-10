"""Step time travel: what the page looked like after each step of a test (opt-in with stepCapture=true).

After each of a test's own steps (and as a step fails) the page is saved: a picture of it, its HTML, its address and
title, and the step's time. The web app shows them as a timeline to scrub through and compares them with the
test's last passing run, so a failure can be understood without running it again.

Files go to <reportsDir>/steps/<test folder>/<n>.jpg|.html|.json, one folder per test and browser, so parallel
workers and browsers never share one. Capturing never fails a test: a page that cannot be pictured is skipped.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

HTML_LIMIT = 2_000_000  # characters of page HTML kept per step
QUALITY = 60


def enabled() -> bool:
    return str(os.getenv("stepCapture") or "").lower() in ("1", "true", "yes", "on")


def folder_name(nodeid: str) -> str:
    """The folder of a test's steps: its pytest node id made safe for a file name (it names test and browser)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nodeid).strip("_")[:150] or "test"


def _target(nodeid: str) -> Path | None:
    root = os.getenv("reportsDir")
    return Path(root) / "steps" / folder_name(nodeid) if root else None


async def capture(nodeid: str, n: int, kind: str, label: str, page, status: str, seconds: float,
                  error: str = "") -> None:
    """Save the page as it is after step `n` (its number in the test case)."""
    target = _target(nodeid)
    if target is None or page is None:
        return
    meta = {"n": n, "kind": kind, "label": label, "status": status, "seconds": seconds, "error": error[:300],
            "at": time.time(), "url": "", "title": "", "image": False, "html": False}
    try:
        target.mkdir(parents=True, exist_ok=True)
        try:
            if not page.is_closed():
                meta["url"] = page.url
                meta["title"] = await page.title()
                await page.screenshot(path=str(target / f"{n}.jpg"), type="jpeg", quality=QUALITY, timeout=5000)
                meta["image"] = True
                html = await page.content()
                (target / f"{n}.html").write_text(html[:HTML_LIMIT], encoding="utf-8")
                meta["html"] = True
        except Exception as e:  # noqa: BLE001 - a closed or navigating page: keep what was saved
            logger.debug("[steps] could not picture step %s: %s", n, e)
        (target / f"{n}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.debug("[steps] could not save step %s", n, exc_info=True)
