"""The results index: runs and their test results in SQLite, so questions about history are queries, not loops.

Every analytic (journey status, trends, flaky tests, a test's recent results, the instance summary) used to walk
every run the app holds in memory and keep the few it needed. The index keeps one row per run (its system,
environment, status, trigger, times, and its run.json as saved) and one row per test result (test, browser, outcome,
duration), so "the finished runs of system S on env E since Monday" or "TC-12's last 10 results" read a handful of
rows through an index whatever the history holds.

Run folders stay the source of truth: the index is rebuilt from them when it is missing, brought up to date at
start-up for folders that changed, written on every save, and a deleted run is removed from both. A run read back
from the index is the run as saved in run.json (webapp.services.run_manager.run_from_dict). Python's own sqlite3,
no server; the schema carries a version and migrations run in order on open.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS = [
    # 1: runs and results
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY, system TEXT NOT NULL, env TEXT NOT NULL, status TEXT NOT NULL,
        trigger TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '', finished_at TEXT NOT NULL DEFAULT '',
        source_mtime INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
    CREATE INDEX runs_scope ON runs (system, env, created_at);
    CREATE INDEX runs_finished ON runs (finished_at);
    CREATE TABLE results (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, nodeid TEXT NOT NULL, test_id TEXT NOT NULL,
        browser TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, duration REAL NOT NULL DEFAULT 0,
        retries INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (run_id, nodeid));
    CREATE INDEX results_test ON results (test_id, browser);
    """,
]
FINISHED = ("passed", "failed", "error", "cancelled")


class ResultsIndex:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._migrate()
        except Exception:
            self._db.close()  # an open handle keeps the file locked on Windows, so it could not be set aside
            raise

    def _migrate(self) -> None:
        with self._lock:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self._db.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
                logger.info("Results index migrated to version %s", number)

    @property
    def version(self) -> int:
        return self._db.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ───── writing ─────

    def put(self, run, source_mtime: int = 0) -> None:
        """Insert or replace a run and its results (call after its run.json is written)."""
        data = run.to_dict()
        rows = [(run.id, r.nodeid, r.test_id, r.browser, r.outcome, float(r.duration or 0), int(r.retries or 0))
                for r in list(run.results.values())]
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.execute("DELETE FROM results WHERE run_id = ?", (run.id,))
                self._db.execute(
                    "INSERT OR REPLACE INTO runs (id, system, env, status, trigger, created_at, finished_at, "
                    "source_mtime, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run.id, run.config.system, run.config.env, run.status, run.trigger or "", run.created_at or "",
                     run.finished_at or "", int(source_mtime), json.dumps(data)))
                self._db.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                self._db.execute("COMMIT")
            except sqlite3.Error:
                self._db.execute("ROLLBACK")
                raise

    def remove(self, run_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def sync(self, runs_dir: Path) -> dict[str, int]:
        """Bring the index in line with the run folders: add new or changed ones, drop the ones gone.
        Returns counts {added, updated, removed, unchanged}."""
        from webapp.services.run_manager import run_from_dict
        runs_dir = Path(runs_dir)
        with self._lock:
            known = dict(self._db.execute("SELECT id, source_mtime FROM runs").fetchall())
        seen, counts = set(), {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
        for meta in sorted(runs_dir.glob("*/run.json")) if runs_dir.is_dir() else []:
            try:
                mtime = meta.stat().st_mtime_ns
                if known.get(meta.parent.name) == mtime:
                    seen.add(meta.parent.name)
                    counts["unchanged"] += 1
                    continue
                run = run_from_dict(json.loads(meta.read_text(encoding="utf-8")), meta.parent)
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("Results index: skipping unreadable %s", meta)
                continue
            seen.add(run.id)
            counts["updated" if run.id in known else "added"] += 1
            self.put(run, mtime)
        for gone in set(known) - seen:
            self.remove(gone)
            counts["removed"] += 1
        return counts

    # ───── reading ─────

    def runs(self, system: str | None = None, env: str | None = None, *, runs_dir: Path | None = None,
             **filters) -> list:
        """Runs (newest first) as Run objects, as saved; filters as for ids()."""
        from webapp.services.run_manager import run_from_dict
        out = []
        for run_id, data in self._select("id, data", system, env, **filters):
            try:
                out.append(run_from_dict(json.loads(data), (Path(runs_dir) / run_id) if runs_dir else Path(run_id)))
            except (ValueError, KeyError, TypeError):
                logger.warning("Results index: unreadable row for %s", run_id)
        return out

    def ids(self, system: str | None = None, env: str | None = None, **filters) -> list[str]:
        """Run ids (newest first) filtered in SQL: system, env, since/until (created_at), finished_only,
        trigger (exact), trigger_prefix, limit."""
        return [row[0] for row in self._select("id", system, env, **filters)]

    def _select(self, columns: str, system: str | None, env: str | None, *, since: str | None = None,
                until: str | None = None, finished_only: bool = False, trigger: str | None = None,
                trigger_prefix: str | None = None, limit: int | None = None) -> list[tuple]:
        where, args = [], []
        for column, value in (("system", system), ("env", env)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if since:
            where.append("created_at >= ?")
            args.append(since)
        if until:
            where.append("created_at < ?")
            args.append(until)
        if finished_only:
            where.append(f"status IN ({','.join('?' * len(FINISHED))})")
            args += list(FINISHED)
        if trigger is not None:
            where.append("trigger = ?")
            args.append(trigger)
        if trigger_prefix:
            where.append("substr(trigger, 1, ?) = ?")
            args += [len(trigger_prefix), trigger_prefix]
        sql = (f"SELECT {columns} FROM runs" + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY created_at DESC, id DESC" + (" LIMIT ?" if limit else ""))
        if limit:
            args.append(int(limit))
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def test_history(self, test_id: str, system: str | None = None, env: str | None = None,
                     limit: int = 20) -> list[dict]:
        """A test's latest results across runs (newest first): run, time, browser, outcome, duration, retries."""
        sql = ("SELECT r.run_id, runs.created_at, r.browser, r.outcome, r.duration, r.retries FROM results r "
               "JOIN runs ON runs.id = r.run_id WHERE r.test_id = ?")
        args: list = [test_id]
        for column, value in (("runs.system", system), ("runs.env", env)):
            if value is not None:
                sql += f" AND {column} = ?"
                args.append(value)
        sql += " ORDER BY runs.created_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        keys = ("run", "at", "browser", "outcome", "duration", "retries")
        return [dict(zip(keys, row)) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            runs = self._db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            results = self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        return {"runs": runs, "results": results}

