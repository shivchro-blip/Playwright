"""Suggest a replacement for a locator that no longer finds its element.

After "Check all locators" lists the fields that broke, this scans the live page (the same visit the Add-fields-from-
the-page scan makes) and ranks the elements it finds against each broken field by what the field still says: its
name (searchBox), its old locator (#search-box), its label, placeholder or text, and its type. Elements that a
working field already uses are left out. The result is a short, scored list per field to review and accept, not an
automatic fix: a redesign can move a label to a different field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from webapp.services.page_scan import Candidate, PageScan, scan_page

KEYS = ("locator", "role", "name", "label", "placeholder", "text", "contains", "index", "nested", "hasTextDirty")
TEXT_KEYS = ("locator", "name", "label", "placeholder", "text", "contains")
STOP = {"the", "a", "an", "of", "to", "and", "or", "for", "is", "input", "field", "btn", "button", "div", "span",
        "type", "text", "id", "name", "class", "data", "testid", "form", "container", "wrapper"}
IDENT = re.compile(r"[#.]([\w-]+)|\[(?:id|name|data-testid)[~^$*|]?=['\"]?([\w-]+)")
MIN_SCORE = 30
TOP = 3


@dataclass
class Suggestion:
    cfg: dict  # locator keys to put on the field (+ index when several elements match)
    type: str
    label: str  # what a person sees
    score: int  # 0-99
    reason: str


@dataclass
class Suggestions:
    ok: bool = False
    error: str = ""
    by_field: dict[str, list[Suggestion]] = field(default_factory=dict)
    scanned: int = 0


def words(value) -> list[str]:
    """Lower-case words of a name, locator or label: camelCase and punctuation split, noise words dropped."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 and w not in STOP and not w.isdigit()]


def squash(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def signature(cfg: dict) -> str:
    return repr(sorted((k, v) for k, v in cfg.items() if k in KEYS))


def _bag(name: str, cfg: dict) -> tuple[dict[str, int], list[str]]:
    """Words the field still says, with weights (its name counts double), and its identifiers for fuzzy matching."""
    bag: dict[str, int] = {}
    for w in words(name):
        bag[w] = max(bag.get(w, 0), 2)
    ids = [squash(name)]
    for key in TEXT_KEYS:
        value = cfg.get(key)
        if value:
            for w in words(value):
                bag.setdefault(w, 1)
            if key == "locator":
                ids += [squash(a or b) for a, b in IDENT.findall(str(value))]
    return bag, [i for i in ids if len(i) > 2]


def rank(name: str, cfg: dict, candidates: list[Candidate], taken: set[str] | None = None) -> list[Suggestion]:
    bag, ids = _bag(name, cfg)
    if not bag and not ids:
        return []
    total = sum(bag.values()) or 1
    out = []
    for c in candidates:
        if signature(c.cfg) in (taken or set()):
            continue
        seen = set(words(c.name)) | set(words(c.label))
        raw = " ".join(str(c.cfg.get(k) or "") for k in TEXT_KEYS)
        seen |= set(words(raw))
        shared = [w for w in bag if w in seen]
        score = round(70 * sum(bag[w] for w in shared) / total)
        reason = [f"shares {', '.join(shared[:4])}"] if shared else []
        cand_ids = [squash(c.name), *[squash(a or b) for a, b in IDENT.findall(raw)]]
        if any(i and i == j for i in ids for j in cand_ids):
            score += 40
            reason.append("same identifier apart from punctuation or case")
        elif any(i and j and (i in j or j in i) and min(len(i), len(j)) >= 4 for i in ids for j in cand_ids):
            score += 20
            reason.append("similar identifier")
        wanted = str(cfg.get("type") or "")
        if wanted and wanted == c.type:
            score += 12
            reason.append(f"same type ({wanted})")
        if score >= MIN_SCORE:
            out.append(Suggestion(dict(c.cfg), c.type, c.label, min(score, 99), "; ".join(reason)))
    out.sort(key=lambda s: (-s.score, s.label))
    return out[:TOP]


def suggest(url: str, broken: dict[str, dict], taken: set[str] | None = None, *,
            storage_state: Path | None = None, scan=scan_page) -> Suggestions:
    """Scan `url` and rank replacements for each broken field (name -> its config)."""
    scanned: PageScan = scan(url, storage_state=storage_state, visible_only=False)
    if not scanned.ok:
        return Suggestions(error=scanned.error or "The page could not be scanned.")
    result = Suggestions(ok=True, scanned=len(scanned.candidates))
    for name, cfg in broken.items():
        result.by_field[name] = rank(name, cfg, scanned.candidates, taken)
    return result
