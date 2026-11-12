"""Self-healing locators (opt-in with selfHeal=true): when a field's locator finds nothing, find the element anyway.

While a run goes well, each field's element is remembered by what it looks like: its id, name, data-testid, label,
placeholder, text and type (a "profile"). When a locator later finds no element at all (an id was renamed in a
redesign), the page is scanned and the elements are ranked against what the field still says plus its profile. The
best one is used for this run only if it is the same kind of element and scores high enough (selfHealMinScore,
default 60), and it matches exactly one element. Every heal is logged as [heal], attached to the test, and written to
the run's results so a person reviews it in the app: accepting updates the page; rejecting stops that replacement
from being used again. The page files are never changed by a run.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CURRENT = {"system": "", "screen": ""}  # set by the suite before each screen step
SKIP_TYPES = {"wait", "keyboard", "waitwhileloading", "pause", "filldate", "Payment", "save", "saveList", "saveCount"}
_profiled: set[tuple[str, str, str]] = set()
_cache: dict[str, tuple[float, dict]] = {}

PROFILE_JS = """(e) => ({
  tag: e.tagName.toLowerCase(), id: e.id || '', name: e.getAttribute('name') || '',
  testid: e.getAttribute('data-testid') || '', type: (e.getAttribute('type') || '').toLowerCase(),
  role: e.getAttribute('role') || '', placeholder: e.getAttribute('placeholder') || '',
  aria: e.getAttribute('aria-label') || '',
  label: (e.labels && e.labels[0] ? e.labels[0].innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 80),
  text: (e.innerText || e.value || '').replace(/\\s+/g, ' ').trim().slice(0, 80)})"""


def enabled() -> bool:
    return str(os.getenv("selfHeal") or "").lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _results_dir() -> Path | None:
    value = os.getenv("reportsDir")
    return Path(value) if value else None


def _append(kind: str, record: dict) -> None:
    folder = _results_dir()
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"autoplay-{kind}-{os.getpid()}.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("[heal] could not write the %s record", kind, exc_info=True)


def _workspace_json(*parts: str) -> dict:
    """A JSON file of the workspace, re-read only when it changes."""
    workspace = os.getenv("AUTOPLAY_WORKSPACE")
    if not workspace:
        return {}
    path = Path(workspace).joinpath(*parts)
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    cached = _cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    _cache[str(path)] = (stamp, data if isinstance(data, dict) else {})
    return _cache[str(path)][1]


def profile_for(system: str, screen: str, field: str) -> dict:
    return ((_workspace_json("Data", "locatorProfiles", f"{system}.json").get(screen) or {}).get(field)) or {}


def rejected() -> set[str]:
    return set(_workspace_json("Data", "heals.json").get("rejected") or [])


def heal_key(system: str, screen: str, field: str, cfg: dict) -> str:
    from webapp.services.locator_suggest import signature
    return f"{system}|{screen}|{field}|{signature(cfg)}"


def skip(field_cfg: dict, action_type: str) -> bool:
    if action_type in SKIP_TYPES or field_cfg.get("nested") or field_cfg.get("tableId"):
        return True
    values = " ".join(str(field_cfg.get(k) or "") for k in ("locator", "name", "label", "placeholder", "text"))
    return "{value}" in values or "DICT." in values or "ENV." in values or not values.strip()


async def capture(element, field: str) -> None:
    """Remember what the element looks like (once per screen and field in this process)."""
    key = (CURRENT["system"], CURRENT["screen"], field)
    if key in _profiled:
        return
    _profiled.add(key)
    try:
        profile = await element.first.evaluate(PROFILE_JS)
    except Exception:  # detached, navigated: nothing to remember
        return
    _append("profiles", {"system": key[0], "screen": key[1], "field": field, "profile": profile,
                         "at": time.strftime("%Y-%m-%dT%H:%M:%S")})


def scoring_config(field_cfg: dict, profile: dict) -> dict:
    """What the field still says, with its remembered profile folded in for the ranking."""
    ids = " ".join(x for x in (
        f"#{profile['id']}" if profile.get("id") else "",
        f"[name='{profile['name']}']" if profile.get("name") else "",
        f"[data-testid='{profile['testid']}']" if profile.get("testid") else "") if x)
    cfg = {k: v for k, v in field_cfg.items() if k in ("locator", "name", "label", "placeholder", "text", "contains",
                                                      "type")}
    cfg["locator"] = " ".join(x for x in (str(field_cfg.get("locator") or ""), ids) if x)
    for key in ("label", "placeholder", "text"):
        if profile.get(key) and not cfg.get(key):
            cfg[key] = profile[key]
    if profile.get("aria") and not cfg.get("label"):
        cfg["label"] = profile["aria"]
    return cfg


async def find(page, field: str, field_cfg: dict):
    """(best Suggestion, scoring config) among the visible elements of the page, or (None, cfg)."""
    from webapp.services.locator_suggest import rank
    from webapp.services.page_scan import ENUMERATE, Candidate, _pascal, candidates_for, field_type

    cfg = scoring_config(field_cfg, profile_for(CURRENT["system"], CURRENT["screen"], field))
    try:
        found = await page.evaluate(ENUMERATE, "")
    except Exception as e:
        logger.warning("[heal] %s: could not scan the page: %s", field, e)
        return None, cfg
    candidates = []
    for d in found:
        options = candidates_for(d, "", 1) if d.get("visible") and not d.get("disabled") else []
        if options:
            label = d["text"] or d["placeholder"] or d["aria"] or d["label"] or d["name"] or d["id"]
            candidates.append(Candidate(_pascal(label) or field_type(d), field_type(d), options[0], label, d["tag"]))
    wanted = str(field_cfg.get("type") or "")
    blocked = rejected()
    for suggestion in rank(field, cfg, candidates):
        if wanted and suggestion.type != wanted:
            continue  # never click a button where a text box was, or the other way round
        if heal_key(CURRENT["system"], CURRENT["screen"], field, suggestion.cfg) in blocked:
            continue
        if suggestion.score >= _int("selfHealMinScore", 60):
            return suggestion, cfg
        break
    return None, cfg


async def resolve(page, element, field: str, field_cfg: dict, action_type: str, locate):
    """The element to act on: the field's own, or a healed one when its locator finds nothing.
    `locate(cfg)` builds a locator from a field config the framework's way."""
    if not enabled() or element is None or skip(field_cfg, action_type):
        return element
    try:
        await element.first.wait_for(state="visible", timeout=_int("selfHealWaitMs", 5000))
        await capture(element, field)
        return element
    except Exception:
        pass
    try:
        if await element.count() > 0:
            return element  # it is there, only not visible: not a locator problem
    except Exception:
        return element
    suggestion, _ = await find(page, field, field_cfg)
    if suggestion is None:
        logger.info("[heal] %s on %s: no element looks like it; the step fails as usual", field, CURRENT["screen"])
        return element
    healed = await locate(suggestion.cfg)
    try:
        matches = await healed.count() if healed is not None else 0
    except Exception:
        matches = 0
    if matches != 1:
        return element
    old = {k: v for k, v in field_cfg.items() if k in ("locator", "role", "name", "label", "placeholder", "text",
                                                       "contains", "index")}
    record = {"system": CURRENT["system"], "screen": CURRENT["screen"], "field": field, "test_id": CURRENT.get("test",
              ""), "old": old, "new": suggestion.cfg, "score": suggestion.score, "reason": suggestion.reason,
              "label": suggestion.label, "url": page.url, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _append("heals", record)
    logger.warning("[heal] %s on %s: %s found nothing; used %s (%s%%: %s)", field, CURRENT["screen"], old,
                   suggestion.cfg, suggestion.score, suggestion.reason)
    try:
        import allure
        allure.attach(json.dumps(record, indent=2), name=f"Healed locator: {CURRENT['screen']}.{field}",
                      attachment_type=allure.attachment_type.JSON)
    except Exception:
        pass
    return healed
