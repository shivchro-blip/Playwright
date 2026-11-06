"""Reads every tenant's /api/summary (in parallel, with its service token) and rolls them up."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from webapp.control.tenants import Tenant, TenantStore
from webapp.services.summary import health

TIMEOUT = 8
CACHE_SECONDS = 10


@dataclass
class TenantView:
    tenant: Tenant
    reachable: bool
    error: str = ""
    summary: dict | None = None
    health: str = "unknown"
    reasons: list[str] = field(default_factory=list)
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return {"tenant": self.tenant.to_dict(), "reachable": self.reachable, "error": self.error,
                "health": self.health, "reasons": self.reasons, "summary": self.summary,
                "fetched_at": self.fetched_at}


def fetch(tenant: Tenant, token: str, timeout: float = TIMEOUT) -> TenantView:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    if not token:
        return TenantView(tenant, False, "No service token: add one for this tenant.", fetched_at=stamp)
    req = urllib.request.Request(f"{tenant.url}/api/summary", headers={"Authorization": f"Bearer {token}",
                                                                      "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            summary = json.loads(r.read())
    except urllib.error.HTTPError as e:
        why = {401: "the service token was refused", 403: "the service token may not read it",
               404: "it does not have /api/summary (an older AutoPlay?)"}.get(e.code, f"HTTP {e.code}")
        return TenantView(tenant, False, f"Reached, but {why}.", fetched_at=stamp)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        return TenantView(tenant, False, f"Not reachable: {reason}", fetched_at=stamp)
    except ValueError:
        return TenantView(tenant, False, "It answered with something that is not a summary.", fetched_at=stamp)
    if not isinstance(summary, dict) or summary.get("app") != "autoplay":
        return TenantView(tenant, False, "It answered with something that is not a summary.", fetched_at=stamp)
    state, reasons = health(summary)
    return TenantView(tenant, True, "", summary, state, reasons, stamp)


class Portfolio:
    def __init__(self, store: TenantStore):
        self.store = store
        self._cache: tuple[float, list[TenantView]] | None = None
        self._lock = threading.Lock()

    def collect(self, fresh: bool = False) -> list[TenantView]:
        with self._lock:
            if not fresh and self._cache and time.monotonic() - self._cache[0] < CACHE_SECONDS:
                return self._cache[1]
        tenants = self.store.list()
        with ThreadPoolExecutor(max_workers=max(1, min(16, len(tenants)))) as pool:
            views = list(pool.map(lambda t: fetch(t, self.store.token(t)), tenants))
        with self._lock:
            self._cache = (time.monotonic(), views)
        return views

    def forget(self) -> None:
        with self._lock:
            self._cache = None


def rollup(views: list[TenantView]) -> dict:
    """Totals across tenants, and everything that needs someone's attention, worst first."""
    by_health = Counter(v.health for v in views)
    attention = []
    for v in views:
        if v.health == "unknown":
            attention.append({"tenant": v.tenant.name, "level": "unknown", "what": v.error})
        for reason in v.reasons:
            attention.append({"tenant": v.tenant.name, "level": v.health, "what": reason})
    order = {"red": 0, "unknown": 1, "amber": 2}
    attention.sort(key=lambda a: (order.get(a["level"], 3), a["tenant"].casefold()))
    sums = Counter()
    for v in views:
        s = v.summary or {}
        sums["journeys_failing"] += (s.get("journeys", {}).get("total", {}) or {}).get("failing", 0)
        sums["journeys_passing"] += (s.get("journeys", {}).get("total", {}) or {}).get("passing", 0)
        sums["monitors_down"] += sum(1 for m in s.get("monitors", []) if m.get("down"))
        sums["runs_active"] += (s.get("runs") or {}).get("active", 0)
        sums["runs_queued"] += (s.get("runs") or {}).get("queued", 0)
        sums["runners_online"] += (s.get("runners") or {}).get("online", 0)
    return {"tenants": len(views), "health": dict(by_health), **sums, "attention": attention}
