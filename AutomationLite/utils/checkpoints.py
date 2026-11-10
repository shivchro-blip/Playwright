"""Run a test from a step: checkpoints (the browser's state after a step) and the options that use them.

With saveCheckpoints=true a run writes, after every top-level step of a test case, <checkpointsDir>/<test>/<n>.json:
the page URL, the browser's cookies and localStorage (Playwright's storage state) and the page's sessionStorage.
A later run of that test case with resumeCheckpoint=<file> starts its browser in that state, signs in only if the
session is gone, opens the saved URL and runs from step runFromStep (up to runToStep).

This reproduces what the portal keeps in the browser or on its server (a session, a saved draft). It cannot
restore what only lived in the page, such as text typed into a form and not yet saved. The files hold session
cookies: they stay in the run folder, are never served by the web app, and go with the run when it is deleted.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_STORAGE_SCRIPT = """(() => {
  const saved = %s;
  if (location.origin === saved.origin) {
    for (const [k, v] of Object.entries(saved.items)) { try { sessionStorage.setItem(k, v); } catch (e) {} }
  }
})();"""
CAPTURE_SESSION_STORAGE = """() => {
  const items = {};
  for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); items[k] = sessionStorage.getItem(k); }
  return {origin: location.origin, items};
}"""


def safe_name(test_id: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", str(test_id)).strip("._") or "test"


def checkpoint_path(base: Path | str, test_id: str, step: int) -> Path:
    return Path(base) / safe_name(test_id) / f"{int(step)}.json"


def _int(name: str) -> int:
    try:
        return max(int(os.getenv(name) or 0), 0)
    except ValueError:
        return 0


def steps_window(total: int) -> tuple[int, int]:
    """(first, last) 1-based step numbers to run, from runFromStep / runToStep; the whole test by default."""
    first, last = _int("runFromStep") or 1, _int("runToStep") or total
    return min(first, max(total, 1)), min(max(last, first), total) if total else 0


def is_windowed(total: int) -> bool:
    return steps_window(total) != (1, total)


def start_url() -> str:
    return (os.getenv("startUrl") or "").strip()


def save_enabled() -> bool:
    return str(os.getenv("saveCheckpoints") or "").lower() in ("1", "true", "yes", "on") and bool(
        os.getenv("checkpointsDir"))


def load_resume(test_id: str) -> dict | None:
    """The checkpoint named by resumeCheckpoint, if it belongs to this test case."""
    path = (os.getenv("resumeCheckpoint") or "").strip()
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Cannot read the saved state {path}: {e}") from e
    if data.get("test_id") != test_id:
        return None  # a different test case in the same run: start it normally
    return data


def session_storage_script(resume: dict | None) -> str | None:
    saved = (resume or {}).get("session_storage")
    if not saved or not saved.get("items"):
        return None
    return SESSION_STORAGE_SCRIPT % json.dumps(saved)


async def save_checkpoint(base: Path | str, test_id: str, step: int, page) -> Path | None:
    """Write the state after `step`. Never raises: a missed checkpoint must not fail the test."""
    try:
        state = await page.context.storage_state()
        try:
            session = await page.evaluate(CAPTURE_SESSION_STORAGE)
        except Exception:  # e.g. about:blank or a page that is navigating
            session = {"origin": "", "items": {}}
        data = {"version": 1, "test_id": test_id, "step": int(step), "url": page.url,
                "saved": datetime.now().isoformat(timespec="seconds"),
                "storage_state": state, "session_storage": session}
        path = checkpoint_path(base, test_id, step)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)
        return path
    except Exception as e:
        logger.warning(f"[checkpoint] Could not save the state after step {step}: {e}")
        return None
