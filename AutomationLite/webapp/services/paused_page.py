"""Look at the page of a paused test (utils/debug_pause.py) from the web app.

A run that pauses on failure launches its Chromium browser with a remote debugging port on this machine (127.0.0.1
only). This connects to it to check a field's locator against the page exactly as the test left it, or to rank
replacements for a locator that finds nothing, without opening another browser and without moving the page.
Playwright's sync API in a worker thread, like the other live checks (the UI calls these through run.io_bound).
Connecting and disconnecting never closes the browser the test is using."""
from __future__ import annotations

from dataclasses import dataclass, field

from webapp.services.locator_check import LocatorCheck, check_open_page
from webapp.services.locator_suggest import Suggestion, rank
from webapp.services.page_scan import scan_open_page


class PausedPageError(Exception):
    pass


def endpoint(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def _open_page(browser, url_hint: str):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages if not pg.is_closed()]
    if not pages:
        raise PausedPageError("The browser has no page open.")
    return next((pg for pg in pages if pg.url == url_hint), pages[-1])


def _connect(p, port: int):
    from playwright.sync_api import Error as PlaywrightError
    try:
        return p.chromium.connect_over_cdp(endpoint(port), timeout=8000)
    except PlaywrightError as e:
        raise PausedPageError("Could not reach the paused browser (it may have ended, or it is not Chromium): "
                              + str(e).splitlines()[0]) from e


def check(port: int, url_hint: str, cfg: dict) -> LocatorCheck:
    """What the locator matches on the paused page now."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _connect(p, port)
        try:
            return check_open_page(_open_page(browser, url_hint), cfg)
        finally:
            browser.close()


@dataclass
class Fixes:
    ok: bool = False
    error: str = ""
    scanned: int = 0
    suggestions: list[Suggestion] = field(default_factory=list)


def suggest(port: int, url_hint: str, name: str, cfg: dict, taken: set[str] | None = None) -> Fixes:
    """Elements of the paused page that could replace the field's locator, best first."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _connect(p, port)
        try:
            page = _open_page(browser, url_hint)
            scanned = scan_open_page(page)
            if not scanned.ok:
                return Fixes(error=scanned.error or "The page could not be scanned.")
            return Fixes(ok=True, scanned=len(scanned.candidates),
                         suggestions=rank(name, cfg, scanned.candidates, taken))
        finally:
            browser.close()
