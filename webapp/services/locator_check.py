"""Live locator check for the Generator: open a page in headless Chromium and see what a field's locator matches.

Builds the Playwright locator with the same precedence as utils.dynamic_form_handler.get_element, so a match
here means the framework will find the element too. Runs Playwright's sync API in the caller's thread (the UI
calls it through nicegui.run.io_bound) so it never depends on the web server's event loop.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MAX_MATCHES = 10
HIGHLIGHT = "3px solid #2851E6"  # --accent: drawn into the screenshot, not the app
SELECTED = "3px solid #B42318"  # --fail: the element `index` picks
TOKEN_RE = re.compile(r"\b(DICT|ENV)\.\w+")


@dataclass
class LocatorCheck:
    ok: bool
    url: str
    description: str = ""  # how the locator was built, e.g. page.get_by_role('button', name='Save')
    count: int = 0  # matches of the locator before `index` is applied
    matches: list[dict] = field(default_factory=list)  # tag, text, visible
    index: int | None = None
    title: str = ""
    final_url: str = ""
    screenshot: bytes | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0


def page_url(base_url: str, page_config: dict) -> str:
    """The URL the framework opens for a page: base_url + path, or fullpath."""
    if page_config.get("path"):
        return f"{base_url}{page_config['path']}"
    return str(page_config.get("fullpath") or base_url or "")


def build_locator(page, cfg: dict):
    """(locator, description) following get_element's order. Raises ValueError for configs it can't check."""
    locator, role, name = cfg.get("locator"), cfg.get("role"), cfg.get("name")
    placeholder, contains, label, text = cfg.get("placeholder"), cfg.get("contains"), cfg.get("label"), cfg.get("text")
    if cfg.get("nested"):
        raise ValueError("Nested locator chains aren't supported by the checker yet.")
    if locator and not role:
        el, desc = page.locator(locator), f"page.locator({locator!r})"
    elif placeholder:
        el, desc = page.get_by_placeholder(placeholder), f"page.get_by_placeholder({placeholder!r})"
    elif contains:
        el, desc = page.locator(f"p:has-text('{contains}')"), f"page.locator(\"p:has-text('{contains}')\")"
    elif label:
        el, desc = page.get_by_label(label), f"page.get_by_label({label!r})"
    elif text:
        if locator:
            el, desc = page.locator(locator).get_by_text(text), f"page.locator({locator!r}).get_by_text({text!r})"
        else:
            el, desc = page.get_by_text(text), f"page.get_by_text({text!r})"
    elif role and name:
        el, desc = page.get_by_role(role, name=name), f"page.get_by_role({role!r}, name={name!r})"
    elif role and locator:
        el = page.get_by_role(role).get_by_text(locator, exact=True)
        desc = f"page.get_by_role({role!r}).get_by_text({locator!r}, exact=True)"
    elif role:
        el, desc = page.get_by_role(role), f"page.get_by_role({role!r})"
    else:
        raise ValueError("The field has no locator, role, placeholder, label, text or contains to check.")
    return el, desc


def check_locator(url: str, cfg: dict, *, storage_state: Path | None = None, timeout_ms: int = 15000,
                  wait_until: str = "load") -> LocatorCheck:
    """Open `url`, resolve the field config, count and highlight the matches, screenshot the viewport."""
    started = time.monotonic()
    result = LocatorCheck(ok=False, url=url)
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        result.error = "Enter a full http(s) URL (the page's path and the system's base_url make one)."
        return result
    for key in ("locator", "text", "name", "label", "placeholder", "contains"):
        if TOKEN_RE.search(str(cfg.get(key) or "")):
            result.warnings.append(f"'{key}' uses DICT./ENV. values, which only exist during a run; "
                                   "they are checked literally here.")
    index = cfg.get("index")
    result.index = int(index) if isinstance(index, (int, float)) or str(index or "").isdigit() else None
    if cfg.get("hasTextDirty"):
        result.warnings.append("hasTextDirty is set: the run applies extra text filters; checked as a plain locator.")

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                state = str(storage_state) if storage_state and Path(storage_state).is_file() else None
                context = browser.new_context(viewport={"width": 1280, "height": 800}, storage_state=state)
                page = context.new_page()
                page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                result.title, result.final_url = page.title(), page.url
                el, result.description = build_locator(page, cfg)
                if result.index is not None:
                    result.description += f".nth({result.index})"
                result.count = el.count()
                if result.index is None and result.count > 1:
                    result.warnings.append(f"The locator matches {result.count} elements: actions like fill and click "
                                           "fail in Playwright's strict mode unless you set index.")
                if result.index is not None and result.index >= result.count:
                    result.warnings.append(f"index {result.index} is out of range: the locator matches "
                                           f"{result.count} element(s), so the run would find nothing.")
                handles = el.element_handles()[:MAX_MATCHES]
                for i, h in enumerate(handles):
                    info = h.evaluate("""(e) => ({tag: e.tagName.toLowerCase(),
                        text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 120)})""")
                    info["visible"] = h.is_visible()
                    info["selected"] = result.index == i
                    result.matches.append(info)
                if result.count:
                    el.evaluate_all(f"""(els) => els.forEach((e, i) => {{
                        e.style.outline = i === {result.index if result.index is not None else -1}
                            ? '{SELECTED}' : '{HIGHLIGHT}';
                        e.style.outlineOffset = '2px'; }})""")
                    target = handles[result.index] if result.index is not None and result.index < len(handles) \
                        else handles[0] if handles else None
                    if target:
                        target.scroll_into_view_if_needed(timeout=2000)
                result.screenshot = page.screenshot(type="png")
                result.ok = True
            finally:
                browser.close()
    except ValueError as e:
        result.error = str(e)
    except PlaywrightError as e:
        result.error = str(e).split("\n=====")[0].strip()[:1500]
    result.seconds = round(time.monotonic() - started, 2)
    return result
