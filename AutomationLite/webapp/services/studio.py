"""Studio: author a test on the live page, one step at a time.

A Studio session is a browser (headless Chromium on the app's machine) showing the system's page. The editor shows
a picture of it; clicking the picture picks the element under the click: what it is (its fingerprint) and every
locator that finds that element and only it (webapp/services/page_scan.py), the best one first, the others kept as
its identity (utils/element_identity.py). The person names it as a field, says what to do (fill, click, select,
check, press a key) with what value, and the step runs at once on the live page, so they see it work before they
move on. Saving writes the fields to a page (merged into an existing one of that name) and the steps as a test case,
the same way a recording is saved (webapp/services/recorder.py: save_recording).

One session per editor; it is closed when the editor closes or the person leaves the page.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from utils.element_identity import make_identity
from webapp.services.locator_check import build_locator
from webapp.services.page_scan import ENUMERATE, candidates_for, field_type
from webapp.services.recorder import Recording, field_name

logger = logging.getLogger(__name__)

VIEWPORT = {"width": 1280, "height": 800}
ACTIONS = ("fill", "click", "select", "check", "press")
TIMEOUT_MS = 8000

# the interactive element under a point: the element there, or its nearest interactive ancestor, marked for the scan
PICK_JS = """([x, y]) => {
  const sel = 'input:not([type=hidden]), textarea, select, button, a[href], [role=button], [role=combobox], ' +
    '[role=checkbox], [role=radio], [role=textbox], [role=tab], [role=link], [role=switch], [role=menuitem], ' +
    '[role=option]';
  let e = document.elementFromPoint(x, y);
  while (e && !e.matches(sel)) e = e.parentElement;
  if (!e) { const label = document.elementFromPoint(x, y)?.closest('label');
            e = label && (label.control || label.querySelector(sel)); }
  if (!e) return null;
  document.querySelectorAll('[data-any-scan]').forEach(el => el.removeAttribute('data-any-scan'));
  return true;
}"""


class StudioError(Exception):
    pass


@dataclass
class Picked:
    """The element a click picked: what to call it, how to find it, and what it is."""
    name: str
    type: str
    cfg: dict  # the best locator that finds only this element (+ index when nothing does)
    identity: dict
    label: str
    tag: str
    matches: int = 1

    @property
    def field(self) -> dict:
        out = {**self.cfg, "type": self.type}
        if self.identity:
            out["identity"] = self.identity
        return out


@dataclass
class Step:
    field: str
    action: str
    value: str = ""
    ok: bool | None = None
    error: str = ""

    @property
    def form_value(self):
        """The step's value as the runner reads it (as a recording would save it)."""
        if self.action == "fill" or self.action == "select":
            return self.value
        if self.action == "check":
            return "check"
        if self.action == "press":
            return {"type": "keyboard", "value": self.value or "Enter"}
        return "click"


@dataclass
class Draft:
    """What is being authored: the page's fields and the test's steps, in order."""
    start_url: str = ""
    fields: dict[str, dict] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)

    def recording(self) -> Recording:
        form: dict = {}
        for step in self.steps:
            key = step.field
            while key in form:  # the same field again: the runner's "name|" convention
                key += "|"
            form[key] = step.form_value
        return Recording(start_url=self.start_url, fields=dict(self.fields), steps=[form] if form else [])


def _check_url(url: str) -> str:
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise StudioError("Give a full http(s) address of the page to open.")
    return url


class StudioSession:
    def __init__(self):
        self._pw = self._browser = self.page = None
        self.id_attribute = ""

    @property
    def open(self) -> bool:
        return self.page is not None and not self.page.is_closed()

    async def start(self, url: str, storage_state: str | None = None) -> None:
        from playwright.async_api import async_playwright
        url = _check_url(url)
        await self.close()
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=True)
            context = await self._browser.new_context(viewport=VIEWPORT, storage_state=storage_state)
            self.page = await context.new_page()
            await self.page.goto(url, wait_until="load", timeout=30000)
            from webapp.services.site_ids import detect_expression
            found = await self.page.evaluate(detect_expression())
            self.id_attribute = found[0] if found else ""
        except Exception:
            await self.close()
            raise

    async def attach(self, endpoint: str, url_hint: str = "") -> None:
        """Look at a page in a browser that is already running (a paused test's: Chromium with a remote debugging
        port) instead of opening one. close() lets go of it without closing the browser."""
        from playwright.async_api import async_playwright
        await self.close()
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(endpoint, timeout=8000)
            pages = [pg for ctx in self._browser.contexts for pg in ctx.pages if not pg.is_closed()]
            if not pages:
                raise StudioError("The browser has no page open.")
            self.page = next((pg for pg in pages if pg.url == url_hint), pages[-1])
            from webapp.services.site_ids import detect_expression
            found = await self.page.evaluate(detect_expression())
            self.id_attribute = found[0] if found else ""
        except Exception:
            await self.close()
            raise

    async def goto(self, url: str) -> None:
        self._need()
        await self.page.goto(_check_url(url), wait_until="load", timeout=30000)

    async def close(self) -> None:
        for thing in (self._browser, self._pw):  # a browser reached by attach() is let go of, not closed
            try:
                if thing is self._pw and thing is not None:
                    await thing.stop()
                elif thing is not None:
                    await thing.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        self._pw = self._browser = self.page = None

    def _need(self) -> None:
        if not self.open:
            raise StudioError("Open a page first.")

    async def screenshot(self) -> str:
        """The page as a data: URL for an image."""
        self._need()
        png = await self.page.screenshot(type="jpeg", quality=75, scale="css")  # clicks are in CSS pixels
        return "data:image/jpeg;base64," + base64.b64encode(png).decode()

    @property
    def url(self) -> str:
        return self.page.url if self.open else ""

    async def pick(self, x: float, y: float, taken: set[str]) -> Picked | None:
        """The interactive element at viewport point (x, y), named and located; None when there is none."""
        self._need()
        if not await self.page.evaluate(PICK_JS, [x, y]):
            return None
        found = await self.page.evaluate(ENUMERATE, self.id_attribute or "")
        target = await self.page.evaluate("""([x, y]) => {
          let e = document.elementFromPoint(x, y);
          const sel = 'input, textarea, select, button, a[href], [role]';
          while (e && !e.matches(sel)) e = e.parentElement;
          if (!e) { const l = document.elementFromPoint(x, y)?.closest('label'); e = l && (l.control || null); }
          return e ? e.getAttribute('data-any-scan') : null; }""", [x, y])
        d = next((item for item in found if str(item["i"]) == str(target)), None)
        if d is None:
            return None
        unique: list[dict] = []
        chosen = None
        for cfg in candidates_for(d, self.id_attribute, 1):
            try:
                locator, _ = build_locator(self.page, cfg)
                markers = await locator.evaluate_all("els => els.map(e => e.getAttribute('data-any-scan'))")
            except Exception:  # noqa: BLE001 - a candidate the page cannot evaluate is just skipped
                continue
            if str(d["i"]) not in markers:
                continue
            if chosen is None:
                chosen = (cfg, len(markers), markers.index(str(d["i"])))
            if markers == [str(d["i"])]:
                unique.append(cfg)
        if chosen is None:
            return None
        cfg, count, position = (unique[0], 1, 0) if unique else chosen
        if count > 1:
            cfg = {**cfg, "index": position}
        kind = field_type(d)
        name = field_name(cfg, kind, taken, self.id_attribute, "")
        label = d["text"] or d["placeholder"] or d["aria"] or d["label"] or d["name"] or d["id"]
        return Picked(name, kind, cfg, make_identity(d, cfg, unique), label, d["tag"], count)

    async def perform(self, cfg: dict, action: str, value: str = "") -> None:
        """Do one step on the live page; StudioError says why it could not."""
        self._need()
        if action not in ACTIONS:
            raise StudioError(f"Unknown action {action!r}.")
        try:
            locator, _ = build_locator(self.page, cfg)
        except ValueError as e:
            raise StudioError(str(e)) from e
        if cfg.get("index") is not None:
            locator = locator.nth(int(cfg["index"]))
        else:
            locator = locator.first
        try:
            if action == "fill":
                await locator.fill(value, timeout=TIMEOUT_MS)
            elif action == "click":
                await locator.click(timeout=TIMEOUT_MS)
            elif action == "select":
                try:
                    await locator.select_option(label=value, timeout=TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - not a label: try it as the option's value
                    await locator.select_option(value=value, timeout=TIMEOUT_MS)
            elif action == "check":
                await locator.check(timeout=TIMEOUT_MS)
            elif action == "press":
                await locator.press(value or "Enter", timeout=TIMEOUT_MS)
            await self.page.wait_for_load_state("load", timeout=TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001 - Playwright's errors are many; the first line says it
            raise StudioError(str(e).splitlines()[0]) from e
