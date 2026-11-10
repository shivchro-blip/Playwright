"""Check every field of a page against the live page in one visit.

One headless browser opens the page once and each field's locator is resolved with the framework's own rules
(locator_check.build_locator): how many elements it matches, whether the one a run would use is visible. After a
site change this turns "the nightly run failed somewhere" into a short list of fields to fix.

A page that needs earlier clicks to reach (a wizard screen) reports its fields missing or hidden: that is a fact
about the page as loaded, not always a broken locator.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from webapp.services.locator_check import TOKEN_RE, build_locator

OK, AMBIGUOUS, OUT_OF_RANGE, HIDDEN, MISSING, SKIPPED, ERROR = (
    "ok", "ambiguous", "out_of_range", "hidden", "missing", "skipped", "error")
PROBLEMS = (MISSING, OUT_OF_RANGE, AMBIGUOUS, HIDDEN, ERROR)  # worst first
LABELS = {OK: "found", AMBIGUOUS: "several matches", OUT_OF_RANGE: "index too high", HIDDEN: "hidden",
          MISSING: "not found", SKIPPED: "not checked", ERROR: "error"}


@dataclass
class FieldHealth:
    name: str
    status: str
    detail: str = ""
    count: int = 0
    cfg: dict = field(default_factory=dict)


@dataclass
class PageHealth:
    url: str
    ok: bool = False
    title: str = ""
    final_url: str = ""
    fields: list[FieldHealth] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.fields:
            out[f.status] = out.get(f.status, 0) + 1
        return out


def _index(cfg: dict) -> int | None:
    value = cfg.get("index")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return int(value) if re.fullmatch(r"\d+", str(value or "").strip()) else None


def skip_reason(cfg: dict) -> str:
    """Why a field cannot be checked on a loaded page, or ''."""
    if cfg.get("nested"):
        return "nested locator chain"
    if cfg.get("tableId") or cfg.get("type") == "table":
        return "table cell (row and column come from the test)"
    if "{value}" in str(cfg.get("locator") or ""):
        return "the test supplies the option ({value})"
    if any(TOKEN_RE.search(str(cfg.get(k) or "")) for k in ("locator", "text", "name", "label", "placeholder")):
        return "uses DICT./ENV. values that exist only during a run"
    if not any(cfg.get(k) for k in ("locator", "role", "placeholder", "label", "text", "contains")):
        return "no locator"
    return ""


def check_fields(url: str, fields: dict[str, dict], *, storage_state: Path | None = None, timeout_ms: int = 15000,
                 wait_until: str = "load", settle_ms: int = 500) -> PageHealth:
    started = time.monotonic()
    result = PageHealth(url=url)
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        result.error = "Enter a full http(s) URL (the page's path and the system's base_url make one)."
        return result

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                state = str(storage_state) if storage_state and Path(storage_state).is_file() else None
                page = browser.new_context(viewport={"width": 1280, "height": 900},
                                           storage_state=state).new_page()
                page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                if settle_ms:
                    page.wait_for_timeout(settle_ms)  # let late-rendered content appear
                result.title, result.final_url = page.title(), page.url
                for name, cfg in fields.items():
                    result.fields.append(_check_one(page, name, cfg, PlaywrightError))
                result.ok = True
            finally:
                browser.close()
    except PlaywrightError as e:
        result.error = str(e).splitlines()[0]
    result.seconds = time.monotonic() - started
    return result


def _check_one(page, name: str, cfg: dict, playwright_error) -> FieldHealth:
    reason = skip_reason(cfg)
    if reason:
        return FieldHealth(name, SKIPPED, reason, 0, cfg)
    try:
        locator, _ = build_locator(page, cfg)
        count = locator.count()
        index = _index(cfg)
        if count == 0:
            return FieldHealth(name, MISSING, "matches nothing on the page", 0, cfg)
        if index is not None:
            if index >= count:
                return FieldHealth(name, OUT_OF_RANGE, f"index {index}, but only {count} match", count, cfg)
            visible = locator.nth(index).is_visible()
            return FieldHealth(name, OK if visible else HIDDEN,
                               "" if visible else f"match {index} is on the page but not visible", count, cfg)
        visible = any(locator.nth(i).is_visible() for i in range(min(count, 10)))
        if count > 1:
            return FieldHealth(name, AMBIGUOUS, f"{count} matches: set index, or the run fails in strict mode",
                               count, cfg)
        return FieldHealth(name, OK if visible else HIDDEN, "" if visible else "found but not visible", count, cfg)
    except ValueError as e:
        return FieldHealth(name, SKIPPED, str(e), 0, cfg)
    except playwright_error as e:
        return FieldHealth(name, ERROR, str(e).splitlines()[0], 0, cfg)
