"""After a run: count its healed locators per test, remember element profiles, and keep heals for review.

The runner (utils/self_heal.py) writes autoplay-heals-*.jsonl and autoplay-profiles-*.jsonl into the run's results.
Profiles go to <workspace>/Data/locatorProfiles/<SYSTEM>.json (what each field's element looked like when it last
worked), which later runs use to heal better. Heals wait in the History tab for a person: accepting one changes the
page (kept in the edit history); rejecting one stops the runner from using that replacement again
(Data/heals.json).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from utils.self_heal import heal_key
from webapp.services.locator_suggest import KEYS as LOCATOR_KEYS
from webapp.services.locator_suggest import signature

logger = logging.getLogger(__name__)
RECENT_RUNS = 50


def read_records(results_dir: Path, kind: str) -> list[dict]:
    out = []
    for path in sorted(Path(results_dir).glob(f"autoplay-{kind}-*.jsonl")) if Path(results_dir).is_dir() else []:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if isinstance(item, dict):
                        out.append(item)
        except (OSError, ValueError):
            logger.warning("Unreadable heal record file %s", path)
    return out


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def after_run(run, workspace: Path) -> int:
    """Mark healed results and keep the profiles. Returns how many heals the run had."""
    heals = read_records(run.results_dir, "heals")
    per_test: dict[str, int] = {}
    for h in heals:
        per_test[str(h.get("test_id", ""))] = per_test.get(str(h.get("test_id", "")), 0) + 1
    for r in list(run.results.values()):
        if r.test_id in per_test:
            r.healed = per_test[r.test_id]
    profiles = read_records(run.results_dir, "profiles")
    by_system: dict[str, list[dict]] = {}
    for p in profiles:
        if p.get("system") and p.get("screen") and p.get("field") and isinstance(p.get("profile"), dict):
            by_system.setdefault(str(p["system"]), []).append(p)
    for system, items in by_system.items():
        path = Path(workspace) / "Data" / "locatorProfiles" / f"{system}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            data = {}
        for p in items:
            data.setdefault(p["screen"], {})[p["field"]] = {**p["profile"], "seen": p.get("at", ""), "run": run.id}
        try:
            _write(path, data)
        except OSError:
            logger.warning("Could not save locator profiles for %s", system, exc_info=True)
    return len(heals)


@dataclass
class PendingHeal:
    key: str
    system: str
    screen: str
    field: str
    old: dict
    new: dict
    score: int
    reason: str
    label: str
    count: int = 0
    runs: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    last_at: str = ""


class HealService:
    def __init__(self, workspace: Path, runs, pom):
        self.workspace, self.runs, self.pom = Path(workspace), runs, pom

    @property
    def decisions_path(self) -> Path:
        return self.workspace / "Data" / "heals.json"

    def decisions(self) -> dict:
        try:
            data = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"accepted": list(data.get("accepted") or []), "rejected": list(data.get("rejected") or []),
                "log": list(data.get("log") or [])}

    def _decide(self, key: str, decision: str, user: str) -> None:
        data = self.decisions()
        if key not in data[decision]:
            data[decision].append(key)
        data["log"].append({"key": key, "decision": decision, "by": user, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        data["log"] = data["log"][-500:]
        _write(self.decisions_path, data)

    def pending(self, system: str | None = None) -> list[PendingHeal]:
        done = set(self.decisions()["accepted"]) | set(self.decisions()["rejected"])
        found: dict[str, PendingHeal] = {}
        recent = [r for r in self.runs.list() if not r.active][:RECENT_RUNS]
        for run in reversed(recent):  # oldest first, so the newest record wins
            for h in read_records(run.results_dir, "heals"):
                if system and h.get("system") != system:
                    continue
                key = heal_key(h["system"], h["screen"], h["field"], h.get("new") or {})
                if key in done or self._already_applied(h):
                    continue
                item = found.setdefault(key, PendingHeal(key, h["system"], h["screen"], h["field"], h.get("old") or {},
                                                         h.get("new") or {}, int(h.get("score") or 0),
                                                         str(h.get("reason") or ""), str(h.get("label") or "")))
                item.count += 1
                item.last_at = str(h.get("at") or run.created_at)
                if run.id not in item.runs:
                    item.runs.append(run.id)
                if h.get("test_id") and h["test_id"] not in item.tests:
                    item.tests.append(h["test_id"])
        return sorted(found.values(), key=lambda p: (-p.count, p.system, p.screen, p.field))

    def _page_rel(self, system: str, screen: str) -> str | None:
        return next((r.rel for r in self.pom.list_pages(system) if Path(r.rel).stem == screen), None)

    def _already_applied(self, h: dict) -> bool:
        rel = self._page_rel(h["system"], h["screen"])
        if not rel:
            return False
        try:
            data, _ = self.pom.load(h["system"], rel)
        except (OSError, ValueError):
            return False
        current = (data.get("fields") or {}).get(h["field"]) or {}
        return signature(current) == signature(h.get("new") or {})

    def accept(self, key: str, user: str) -> PendingHeal:
        item = next((p for p in self.pending() if p.key == key), None)
        if item is None:
            raise ValueError("That heal is no longer waiting for review.")
        rel = self._page_rel(item.system, item.screen)
        if rel is None:
            raise ValueError(f"The page {item.screen} is not in {item.system} any more.")
        data, sha = self.pom.load(item.system, rel)
        fields = data.setdefault("fields", {})
        if item.field not in fields:
            raise ValueError(f"{item.screen} has no field {item.field} any more.")
        kept = {k: v for k, v in fields[item.field].items() if k not in LOCATOR_KEYS}
        fields[item.field] = {**kept, **{k: v for k, v in item.new.items() if k in LOCATOR_KEYS}}
        self.pom.save(item.system, rel, data, sha)  # the edit history keeps the page as it was
        self._decide(key, "accepted", user)
        return item

    def reject(self, key: str, user: str) -> None:
        self._decide(key, "rejected", user)
