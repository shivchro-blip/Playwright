"""Element identity: what a page field's element *is*, and the ranked locators known to find it.

A field in a page file keeps its locator as before, and may also carry

    "identity": {"fingerprint": {"tag": "input", "id": "q", "name": "q", "label": "Search", ...},
                 "locators": [{"locator": "input[name='q']"}, {"label": "Search"}, ...],
                 "verified": "2026-11-07T10:00:00"}

The fingerprint is what the element looked like when it was captured (by the recorder or "From page"): its tag,
type, role, id, name, test id, label, placeholder, aria label and text. The locators are other ways to find the same
element, each checked against the live page when it was captured, best first. Files without "identity" work exactly
as before, and older versions of AutoPlay ignore the key.

During a run, for a field that has an identity:
  - its own locator finds the element: it is used. When that element no longer looks like the fingerprint (the page
    changed under the locator), a "drift" record says so, and the element is still used: identity never swaps an
    element that was found;
  - its own locator finds nothing: the identity's locators are tried in order, and the first that finds exactly one
    element that looks like the fingerprint (similarity >= IDENTITY_MIN_SCORE) is used for this run. A "fallback"
    record says which one, so the field's health drops and the app can offer to promote it;
  - nothing works: a "missing" record, and the step fails as it always did.
Records go to the run's results (autoplay-identity-<pid>.jsonl); page files are never changed by a run.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

KEYS = ("tag", "type", "role", "id", "name", "testid", "label", "placeholder", "aria", "text")
# how much each trait says about which element this is; tag and input type must match outright
WEIGHTS = {"testid": 30, "id": 20, "name": 20, "label": 20, "text": 15, "aria": 10, "placeholder": 10, "role": 5}
LOCATOR_KEYS = ("locator", "role", "name", "label", "placeholder", "text", "contains", "index")
MIN_SCORE = 60
WAIT_MS = 3000

FINGERPRINT_JS = """(e) => ({
  tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
  role: e.getAttribute('role') || '', id: e.id || '', name: e.getAttribute('name') || '',
  testid: e.getAttribute('data-testid') || '', placeholder: e.getAttribute('placeholder') || '',
  aria: e.getAttribute('aria-label') || '',
  label: (e.labels && e.labels[0] ? e.labels[0].innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 80),
  text: (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80)})"""


def fingerprint(element: dict) -> dict:
    """The traits worth keeping from a scanned element (page_scan's ENUMERATE or FINGERPRINT_JS), non-empty only."""
    out = {}
    for key in KEYS:
        value = str(element.get(key) or "").strip()
        if value:
            out[key] = value.lower() if key in ("tag", "type") else value
    return out


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def similarity(expected: dict, observed: dict) -> int:
    """0-100: how much the element observed looks like the fingerprint. A different tag, or input type, is 0."""
    if not expected:
        return 0
    if expected.get("tag") and observed.get("tag") and expected["tag"] != observed["tag"]:
        return 0
    if expected.get("tag") == "input" and (expected.get("type") or "text") != (observed.get("type") or "text"):
        return 0
    have = matched = 0
    for key, weight in WEIGHTS.items():
        if expected.get(key):
            have += weight
            if observed.get(key) and _same(str(expected[key]), str(observed[key])):
                matched += weight
    return round(100 * matched / have) if have else (100 if expected.get("tag") else 0)


def primary_of(cfg: dict) -> dict:
    return {k: cfg[k] for k in LOCATOR_KEYS if cfg.get(k) not in (None, "")}


def identity_of(cfg: dict) -> dict:
    ident = cfg.get("identity") if isinstance(cfg, dict) else None
    return ident if isinstance(ident, dict) and isinstance(ident.get("fingerprint"), dict) else {}


def make_identity(fp: dict, primary: dict, candidates: list[dict], verified: str = "") -> dict:
    """An identity block: the fingerprint and the candidates other than the primary, in their order, once each."""
    seen, ranked = {repr(sorted(primary_of(primary).items()))}, []
    for cand in candidates:
        cand = primary_of(cand)
        key = repr(sorted(cand.items()))
        if cand and key not in seen:
            seen.add(key)
            ranked.append(cand)
    return {"fingerprint": fingerprint(fp), "locators": ranked,
            "verified": verified or time.strftime("%Y-%m-%dT%H:%M:%S")}


def min_score() -> int:
    try:
        return int(os.getenv("identityMinScore") or MIN_SCORE)
    except ValueError:
        return MIN_SCORE


def _wait_ms() -> int:
    try:
        return int(os.getenv("identityWaitMs") or WAIT_MS)
    except ValueError:
        return WAIT_MS


def _record(kind: str, field: str, **details) -> None:
    from utils import self_heal
    record = {"system": self_heal.CURRENT.get("system", ""), "screen": self_heal.CURRENT.get("screen", ""),
              "field": field, "test_id": self_heal.CURRENT.get("test", ""), "outcome": kind,
              "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **details}
    self_heal._append("identity", record)


_seen: set[tuple] = set()


def _once(kind: str, field: str, **details) -> None:
    """One record per test, screen and field for the common outcome (the locator worked)."""
    from utils import self_heal
    key = (kind, self_heal.CURRENT.get("test", ""), self_heal.CURRENT.get("screen", ""), field)
    if key not in _seen:
        _seen.add(key)
        _record(kind, field, **details)


async def _observe(element) -> dict:
    try:
        return await element.first.evaluate(FINGERPRINT_JS)
    except Exception:
        return {}


async def resolve(element, field: str, cfg: dict, locate):
    """The element to act on, using the field's identity when its own locator finds nothing.
    `locate(cfg)` builds a locator from a field config the framework's way. Fields without identity: unchanged."""
    ident = identity_of(cfg)
    if not ident or element is None:
        return element
    expected = ident["fingerprint"]
    try:
        await element.first.wait_for(state="attached", timeout=_wait_ms())
        found = await element.count()
    except Exception:
        found = 0
    if found:
        score = similarity(expected, await _observe(element)) if found == 1 else None
        if score is not None and score < min_score():
            logger.warning("[identity] %s: its locator finds an element that no longer looks like it (%s%%)",
                           field, score)
            _record("drift", field, score=score)
        else:
            _once("ok", field, score=score)
        return element
    for n, cand in enumerate(ident.get("locators") or []):
        try:
            other = await locate(cand)
            if other is None or await other.count() != 1:
                continue
            score = similarity(expected, await _observe(other))
        except Exception:
            continue
        if score >= min_score():
            logger.warning("[identity] %s: its locator found nothing; used fallback %s %s (%s%%)", field, n + 1,
                           cand, score)
            _record("fallback", field, used=n, locator=cand, score=score, primary=primary_of(cfg))
            return other
    logger.info("[identity] %s: neither its locator nor its %s fallback(s) found it", field,
                len(ident.get("locators") or []))
    _record("missing", field, tried=len(ident.get("locators") or []))
    return element
