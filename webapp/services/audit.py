"""Audit trail: who did what, when. One JSON line per event in <workspace>/reports/audit.jsonl."""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, user: str, action: str, target: str = "", **details) -> None:
        entry = {"at": datetime.now().isoformat(timespec="seconds"), "user": user, "action": action,
                 "target": target, **{k: v for k, v in details.items() if v not in (None, "", [], {})}}
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            logger.exception("Could not write the audit log")

    def recent(self, limit: int = 200, user: str | None = None, action: str | None = None) -> list[dict]:
        """Newest first."""
        if not self.path.is_file():
            return []
        keep: deque = deque(maxlen=limit)
        with self.path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if (user is None or entry.get("user") == user) and (action is None or entry.get("action") == action):
                    keep.append(entry)
        return list(reversed(keep))
