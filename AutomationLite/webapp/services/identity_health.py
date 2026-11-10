"""How healthy each field's element identity is, from what recent runs saw (utils/element_identity.py).

Every run writes, per test and field with an identity, one of: ok (its locator found the element and it still looks
like the fingerprint), fallback (its locator found nothing and a ranked fallback was used), drift (its locator found an
element that no longer looks like it), missing (nothing found it). Over the latest RECENT_RUNS finished runs, a field
scores 100 when every look was ok, half a point for each fallback or drift, nothing for a miss. A field that keeps
using the same fallback is worth promoting: that fallback becomes its locator and the old locator a fallback.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from utils.element_identity import LOCATOR_KEYS, primary_of
from webapp.services.heals import read_records

RECENT_RUNS = 30
OUTCOMES = ("ok", "fallback", "drift", "missing")


@dataclass
class FieldHealth:
    system: str
    screen: str
    field: str
    counts: Counter = field(default_factory=Counter)
    fallbacks: Counter = field(default_factory=Counter)  # repr(locator) -> uses
    fallback_cfg: dict = field(default_factory=dict)  # the fallback used most
    last_at: str = ""

    @property
    def looks(self) -> int:
        return sum(self.counts[o] for o in OUTCOMES)

    @property
    def score(self) -> int | None:
        if not self.looks:
            return None
        good = self.counts["ok"] + 0.5 * (self.counts["fallback"] + self.counts["drift"])
        return round(100 * good / self.looks)

    @property
    def summary(self) -> str:
        parts = [f"{self.counts[o]} {o}" for o in OUTCOMES if self.counts[o]]
        return ", ".join(parts) or "not used in recent runs"


def collect(runs, system: str | None = None) -> dict[tuple[str, str, str], FieldHealth]:
    """(system, screen, field) -> its health over the runs given (newest first; the latest RECENT_RUNS count)."""
    out: dict[tuple[str, str, str], FieldHealth] = {}
    for run in list(runs)[:RECENT_RUNS]:
        for rec in read_records(run.results_dir, "identity"):
            key = (str(rec.get("system", "")), str(rec.get("screen", "")), str(rec.get("field", "")))
            if system and key[0] != system or rec.get("outcome") not in OUTCOMES:
                continue
            health = out.setdefault(key, FieldHealth(*key))
            health.counts[rec["outcome"]] += 1
            health.last_at = max(health.last_at, str(rec.get("at", "")))
            if rec["outcome"] == "fallback" and isinstance(rec.get("locator"), dict):
                health.fallbacks[repr(sorted(rec["locator"].items()))] += 1
                top = health.fallbacks.most_common(1)[0][0]
                if top == repr(sorted(rec["locator"].items())):
                    health.fallback_cfg = dict(rec["locator"])
    return out


def promote(cfg: dict, fallback: dict) -> dict:
    """The field with `fallback` as its locator; the old locator becomes the first fallback. Other keys are kept."""
    from utils.element_identity import identity_of
    old = primary_of(cfg)
    new = {k: v for k, v in cfg.items() if k not in LOCATOR_KEYS}
    new.update(primary_of(fallback))
    ident = dict(identity_of(cfg))
    if ident:
        rest = [c for c in ident.get("locators") or [] if primary_of(c) != primary_of(fallback)]
        ident["locators"] = ([old] if old else []) + [c for c in rest if c != old]
        new["identity"] = ident
    return new


@dataclass
class Learned:
    field: str
    identity: dict  # {} when the field's element was not found on the page
    note: str


def learn(fields: dict[str, dict], candidates) -> list[Learned]:
    """Identities for existing fields from a page scan (webapp/services/page_scan.py): a field gets the identity of
    the scanned element whose chosen locator is exactly the field's own. No guessing: a field whose locator the
    scan did not choose for any element is reported, not matched."""
    from utils.element_identity import identity_of
    by_locator = {repr(sorted(primary_of(c.cfg).items())): c for c in candidates}
    out = []
    for name, cfg in fields.items():
        if not isinstance(cfg, dict) or not primary_of(cfg):
            continue
        found = by_locator.get(repr(sorted(primary_of(cfg).items())))
        if found is None or not found.identity:
            out.append(Learned(name, {}, "not found on this page with its locator (hidden, on another step of the "
                                         "page, or the locator is broken)"))
            continue
        n = len(found.identity.get("locators") or [])
        had = " (replaces its identity)" if identity_of(cfg) else ""
        out.append(Learned(name, found.identity, f"{n} fallback locator{'' if n == 1 else 's'}{had}"))
    return out
