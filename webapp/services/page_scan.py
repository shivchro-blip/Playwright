"""Add fields from a live page: open it in headless Chromium, list what a person can interact with, and propose a
name, type and locator for each element.

Locators are built in the keys the framework's get_element understands and every one is checked against the page
with the same resolution the run uses (locator_check.build_locator), so what is proposed is found at run time too.
Order of preference: the site's own id attribute (for example data-attribute-id), #id, data-testid, name,
role + accessible name, placeholder, label, then a class. A locator that matches several elements gets the
index of the element it was proposed for.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from webapp.services.locator_check import build_locator
from webapp.services.recorder import field_name

MAX_ELEMENTS = 300
ATTR_RE = re.compile(r"^[A-Za-z_][\w\-:.]*$")
UNSTABLE_ID = re.compile(r"(\d{4,})|(^:)|([a-f0-9]{8}-[a-f0-9]{4})")
GENERATED_CLASS = re.compile(r"^(css-|Mui|jss|sc-|svelte-|ng-|_)|\d{3,}")

ENUMERATE = """(attr) => {
  const sel = 'input:not([type=hidden]), textarea, select, button, a[href], [role=button], [role=combobox], ' +
    '[role=checkbox], [role=radio], [role=textbox], [role=tab], [role=link], [role=switch], [role=menuitem], ' +
    '[role=option]';
  const clean = (t) => (t || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const out = []; let n = 0;
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    const visible = r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
    e.setAttribute('data-any-scan', String(n));
    const box = attr ? e.closest('[' + attr + ']') : null;
    out.push({i: n, tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
      role: e.getAttribute('role') || '', text: clean(e.innerText),
      aria: clean(e.getAttribute('aria-label')), placeholder: clean(e.getAttribute('placeholder')),
      name: e.getAttribute('name') || '', id: e.id || '', testid: e.getAttribute('data-testid') || '',
      label: clean(e.labels && e.labels[0] ? e.labels[0].innerText : ''),
      cls: [...e.classList], container: box ? box.getAttribute(attr) : '', self: !!box && box === e,
      visible, disabled: e.disabled === true});
    n++;
  }
  return out;
}"""


@dataclass
class Candidate:
    name: str
    type: str
    cfg: dict  # locator keys (+ index when several elements match)
    label: str  # what a person sees: the text, placeholder or label
    tag: str
    matches: int = 1  # elements the locator matches before index
    visible: bool = True
    disabled: bool = False


@dataclass
class PageScan:
    url: str
    ok: bool = False
    title: str = ""
    final_url: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    skipped: int = 0  # elements no reliable locator could be built for
    total: int = 0
    error: str = ""
    seconds: float = 0.0


def _pascal(text: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", text))[:24]


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def field_type(d: dict) -> str:
    tag, kind, role = d["tag"], d["type"].lower(), d["role"].lower()
    if tag == "select" or role == "combobox":
        return "select"
    if tag == "textarea":
        return "textarea"
    if tag == "input":
        return {"checkbox": "checkbox", "radio": "radio", "file": "document", "number": "number",
                "submit": "button", "button": "button", "reset": "button", "image": "button"}.get(kind, "text")
    if role in ("checkbox", "switch"):
        return "checkbox"
    if role == "radio":
        return "radio"
    if role == "textbox":
        return "text"
    if tag == "a" or role == "link":
        return "link"
    return "button"


def _kind_selector(d: dict) -> str:
    """The element's own selector inside a container: input[type='checkbox'], button, a, [role='combobox']."""
    if d["tag"] == "input":
        return f"input[type='{_q(d['type'])}']" if d["type"] and d["type"] not in ("text",) else "input"
    if d["tag"] in ("button", "a", "textarea", "select"):
        return d["tag"]
    return f"[role='{_q(d['role'])}']" if d["role"] else d["tag"]


def _accessible(d: dict) -> str:
    return d["aria"] or d["label"] or (d["text"] if d["tag"] in ("button", "a") or d["role"] else "")


def candidates_for(d: dict, attr: str, peers: int) -> list[dict]:
    """Locator configs for one element, best first. `peers`: same-kind elements in its id container."""
    out: list[dict] = []
    if attr and d["container"]:
        base = f"[{attr}='{_q(d['container'])}']"
        if d["self"]:
            out.append({"locator": base})
        else:
            inner = _kind_selector(d)
            if peers > 1 and d["text"] and d["tag"] in ("button", "a") or (peers > 1 and d["role"] and d["text"]):
                out.append({"locator": f"{base} {inner}:text-is('{_q(d['text'])}')"})
            out.append({"locator": f"{base} {inner}"})
    if d["id"] and not UNSTABLE_ID.search(d["id"]):
        out.append({"locator": f"#{d['id']}"})
    if d["testid"]:
        out.append({"locator": f"[data-testid='{_q(d['testid'])}']"})
    if d["name"] and d["tag"] in ("input", "textarea", "select"):
        out.append({"locator": f"{d['tag']}[name='{_q(d['name'])}']"})
    role = d["role"] or {"button": "button", "a": "link", "textarea": "textbox", "select": "combobox"}.get(d["tag"], "")
    if d["tag"] == "input":
        role = {"checkbox": "checkbox", "radio": "radio", "button": "button", "submit": "button",
                "text": "textbox", "email": "textbox", "": "textbox"}.get(d["type"].lower(), "")
    if role and _accessible(d):
        out.append({"role": role, "name": _accessible(d)})
    if d["placeholder"]:
        out.append({"placeholder": d["placeholder"]})
    if d["label"] and d["tag"] in ("input", "textarea", "select"):
        out.append({"label": d["label"]})
    for cls in d["cls"]:
        if re.fullmatch(r"[A-Za-z][\w\-]{0,29}", cls) and not GENERATED_CLASS.search(cls):
            out.append({"locator": f"{d['tag']}.{cls}"})
            break
    return out


def scan_page(url: str, *, id_attribute: str = "", id_prefix: str = "", storage_state: Path | None = None,
              timeout_ms: int = 15000, wait_until: str = "load", visible_only: bool = True) -> PageScan:
    started = time.monotonic()
    result = PageScan(url=url)
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        result.error = "Enter a full http(s) URL (the page's path and the system's base_url make one)."
        return result
    if id_attribute and not ATTR_RE.match(id_attribute):
        result.error = f"{id_attribute!r} is not an attribute name."
        return result

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                state = str(storage_state) if storage_state and Path(storage_state).is_file() else None
                page = browser.new_context(viewport={"width": 1280, "height": 900}, storage_state=state).new_page()
                page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                result.title, result.final_url = page.title(), page.url
                found = page.evaluate(ENUMERATE, id_attribute or "")
                result.total = len(found)
                shown = [d for d in found if d["visible"] or not visible_only][:MAX_ELEMENTS]
                groups: dict[tuple, int] = {}
                for d in shown:
                    groups[(d["container"], _kind_selector(d))] = groups.get((d["container"], _kind_selector(d)), 0) + 1
                taken: set[str] = set()
                seen: set[str] = set()
                for d in shown:
                    peers = groups[(d["container"], _kind_selector(d))]
                    chosen = None
                    for cfg in candidates_for(d, id_attribute, peers):
                        try:
                            locator, _ = build_locator(page, cfg)
                            markers = locator.evaluate_all("els => els.map(e => e.getAttribute('data-any-scan'))")
                        except (ValueError, PlaywrightError):
                            continue
                        if str(d["i"]) in markers:
                            chosen = (cfg, len(markers), markers.index(str(d["i"])))
                            break
                    if chosen is None:
                        result.skipped += 1
                        continue
                    cfg, count, position = chosen
                    if count > 1:
                        cfg = {**cfg, "index": position}
                    kind = field_type(d)
                    key = repr(sorted(cfg.items()))
                    if key in seen:
                        continue
                    seen.add(key)
                    if id_attribute and d["container"] and peers > 1 and d["text"] and not d["self"]:
                        # several choices share one id (Yes / No): the id plus what each one says
                        ident = d["container"]
                        ident = ident[len(id_prefix):] if id_prefix and ident.startswith(id_prefix) else ident
                        base = (_pascal(ident) + _pascal(d["text"])) or kind
                        name, n = base, 2
                        while name in taken:
                            name, n = f"{base}{n}", n + 1
                    else:
                        by_class = re.fullmatch(r"[a-z0-9]+\.([\w-]+)", str(cfg.get("locator") or ""))
                        named = {"label": re.sub(r"[-_]+", " ", by_class.group(1))} if by_class else cfg
                        name = field_name(named, kind, taken, id_attribute, id_prefix)
                    taken.add(name)
                    label = d["text"] or d["placeholder"] or d["aria"] or d["label"] or d["name"] or d["id"]
                    result.candidates.append(Candidate(name, kind, cfg, label, d["tag"], count, d["visible"],
                                                       d["disabled"]))
                result.ok = True
            finally:
                browser.close()
    except PlaywrightError as e:
        result.error = str(e).splitlines()[0]
    result.seconds = time.monotonic() - started
    return result
