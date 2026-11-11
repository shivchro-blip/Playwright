"""Pause on failure: keep the browser open when a step fails, and resume after the test or page JSON was fixed.

Off unless the run has debugOnFailure=true AND the web app gave it a debugDir (a scheduled run, CI or a command-line
run never pauses, whatever .env says). When on, a failure inside a step does not end the test: the test waits, with its
browser, its page and everything it saved earlier (DICT values, the login) still alive, until the person says what
to do. The web app and this process talk through files in <debugDir>:

    command.json      written by the web app: {"do": "retry" | "restep" | "skip" | "abort", "seq": N}
    paused.png        the page when the step failed

and through events on stdout (@@AUTOPLAY@@ lines, the way webapp/pytest_events.py does):
    {"event": "debug", "phase": "paused", "seq", "test", "step", "of", "kind", "label", "screen", "field", "action",
     "error", "url", "can_skip", "notice", "timeout_minutes"}
    {"event": "debug", "phase": "resumed", "seq", "do"}

What happens on each command (run_steps in tests/dynamic_test_suite.py does the re-running):
    retry    re-read the test case and the pages from disk, run the failed field again, carry on
    restep   the same, from the step's first field
    skip     the same, leaving the failed field out
    abort    the failure stands: the test fails the usual way and the browser closes

Never waits forever: no command for debugTimeoutMinutes (default 30), the browser window closed, or the web app gone
(on systems where that can be seen) ends the pause as an abort.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PREFIX = "@@AUTOPLAY@@ "  # webapp/pytest_events.PREFIX; utils never imports the web app
POLL_SECONDS = 0.3
DEFAULT_TIMEOUT_MINUTES = 30
COMMANDS = ("retry", "restep", "skip", "abort")
ERROR_CHARS = 600

_PARENT = os.getppid()
_seq = 0
# Where the run is, for a failure to say which field it was: actions finished before the one that ran last, its
# field, and (inside a common action) which of its screens
progress: dict = {"completed": 0, "field": "", "type": "", "screen": "", "sub": None}
_resume: dict = {"sub": None, "actions": 0}  # what the next run of the failed step skips
summary: dict = {"pauses": 0, "retries": 0, "restarts": 0, "skips": 0, "aborts": 0}


def enabled() -> bool:
    return str(os.getenv("debugOnFailure") or "").strip().lower() in ("true", "1", "yes") \
        and bool(os.getenv("debugDir"))


def _folder() -> Path:
    return Path(os.environ["debugDir"])


@dataclass
class Decision:
    steps: list  # the steps still to run, as they are on disk now (the failed one first)
    offset: int  # how many steps of the test's list come before them


# ───────────── where the run is (cheap bookkeeping; does nothing unless a failure asks) ─────────────

def begin_step() -> None:
    progress.update(completed=0, field="", type="", screen="", sub=None)


def begin_screen(screen: str) -> None:
    progress.update(completed=0, field="", type="", screen=screen)


def note_action(done: int, action: dict) -> None:
    """The action about to run is number done+1 of its screen."""
    progress.update(completed=done, field=str(action.get("field") or ""), type=str(action.get("type") or ""))


def note_actions_done(count: int) -> None:
    """Every action ran; what can still fail is the check for error messages on the page."""
    progress.update(completed=count, field="", type="")


def note_sub(loop_no: int, index: int) -> None:
    progress["sub"] = (loop_no, index)


def take_action_resume() -> int:
    """Actions of the next screen to leave out because they already ran (once)."""
    n, _resume["actions"] = _resume["actions"], 0
    return n


def skip_sub(loop_no: int, index: int) -> bool:
    """Inside a common action being resumed: True for the screens that already ran, False from the failed one on."""
    want = _resume["sub"]
    if want is None:
        return False
    if (loop_no, index) < tuple(want):
        return True
    _resume["sub"] = None
    return False


def clear_resume() -> None:
    _resume.update(sub=None, actions=0)


# ───────────── the pause ─────────────

def pausable(error: BaseException) -> bool:
    """A failure of the test (a failed check or a Playwright error), not a cancel, an exit or a skip."""
    if not enabled() or getattr(error, "_autoplay_debugged", False):
        return False
    import pytest
    return isinstance(error, (Exception, pytest.fail.Exception))


def _emit(**payload) -> None:
    out = sys.__stdout__ or sys.stdout
    out.write(PREFIX + json.dumps({"event": "debug", **payload}, default=str) + "\n")
    out.flush()


def _first_line(error: BaseException) -> str:
    text = str(getattr(error, "msg", "") or error).strip()
    return (text.splitlines() or [type(error).__name__])[0][:ERROR_CHARS]


def _timeout_minutes() -> float:
    try:
        return max(float(os.getenv("AUTOPLAY_DEBUG_TIMEOUT_MINUTES") or DEFAULT_TIMEOUT_MINUTES), 0.05)
    except ValueError:
        return DEFAULT_TIMEOUT_MINUTES


def _write_summary() -> None:
    try:
        folder = _folder()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    except OSError:
        logger.debug("[debug] could not write the summary", exc_info=True)


def _parent_gone() -> bool:
    # On Windows a dead parent's id is still reported, so only the timeout and Cancel end a pause there
    return sys.platform != "win32" and os.getppid() != _PARENT


async def _wait_for_command(page, seq: int, minutes: float) -> str:
    """The person's command for pause number seq; 'abort' when nobody can answer any more."""
    command_file = _folder() / "command.json"
    deadline = time.monotonic() + minutes * 60
    while True:
        if command_file.exists():
            try:
                data = json.loads(command_file.read_text(encoding="utf-8"))
                command_file.unlink(missing_ok=True)
            except (OSError, ValueError):
                await asyncio.sleep(POLL_SECONDS)  # being written
                continue
            if data.get("seq") == seq and data.get("do") in COMMANDS:
                return data["do"]
            logger.warning("[debug] ignored a stale or unknown command: %s", data)
        if time.monotonic() > deadline:
            logger.warning("[debug] no answer in %s minutes: ending the pause", minutes)
            return "abort"
        if _parent_gone():
            logger.warning("[debug] the web app is gone: ending the pause")
            return "abort"
        try:
            if page.is_closed():
                logger.warning("[debug] the page was closed: ending the pause")
                return "abort"
        except Exception:  # noqa: BLE001 - a page that cannot even be asked is not coming back
            return "abort"
        await asyncio.sleep(POLL_SECONDS)


async def on_failure(error: BaseException, *, page, step_no: int, step: dict, of: int, kind: str, label: str,
                     reload_steps, test_id: str = "") -> Decision | None:
    """Pause on this failure. Returns what to run next, or None when the failure stands (abort)."""
    global _seq
    where = dict(progress)
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "command.json").unlink(missing_ok=True)
    summary["pauses"] += 1
    notice = ""
    while True:
        _seq += 1
        seq = _seq
        url = ""
        try:
            url = page.url
            await page.screenshot(path=str(folder / "paused.png"), timeout=5000)
        except Exception:  # noqa: BLE001 - a picture is a help, not a requirement
            logger.debug("[debug] no picture of the page", exc_info=True)
        field = where["field"]
        logger.warning("[debug] PAUSED at step %s (%s)%s: %s. The browser stays open.", step_no, label,
                       f", field '{field}'" if field else "", _first_line(error))
        _emit(phase="paused", seq=seq, test=test_id, step=step_no, of=of, kind=kind, label=label,
              screen=where["screen"] or (label if kind == "page" else ""), field=field, action=where["type"],
              action_no=where["completed"] + 1, error=_first_line(error), url=url, can_skip=bool(field),
              notice=notice, timeout_minutes=_timeout_minutes(), has_picture=(folder / "paused.png").exists())
        _write_summary()
        do = await _wait_for_command(page, seq, _timeout_minutes())
        _emit(phase="resumed", seq=seq, do=do)
        if do == "abort":
            summary["aborts"] += 1
            _write_summary()
            try:
                error._autoplay_debugged = True  # the outer steps must not pause on the same failure again
            except Exception:  # noqa: BLE001
                pass
            return None
        try:
            fresh = reload_steps() if reload_steps else None
        except Exception as e:  # noqa: BLE001 - an unreadable file is something to fix, not a crash
            notice = f"Could not read the edited files: {e}"
        else:
            if not fresh or len(fresh) < step_no:
                notice = (f"The test now has {len(fresh or [])} steps; step {step_no}, where it stopped, is gone. "
                          "Put it back, or abort.")
            else:
                break
        logger.warning("[debug] %s", notice)
    sub = where["sub"]
    if do == "retry":
        _resume.update(sub=sub, actions=where["completed"])
        summary["retries"] += 1
    elif do == "skip":
        _resume.update(sub=sub, actions=where["completed"] + 1)
        summary["skips"] += 1
    else:  # restep: the whole step again
        _resume.update(sub=None, actions=0)
        summary["restarts"] += 1
    _write_summary()
    logger.warning("[debug] RESUMING (%s) from step %s, in the same browser", do, step_no)
    return Decision(steps=list(fresh[step_no - 1:]), offset=step_no - 1)
