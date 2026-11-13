"""Pause on failure: keep the browser open when a step fails, and resume after the test or page JSON was fixed.

Off unless the run has debugOnFailure=true AND the web app gave it a debugDir (a scheduled run, CI or a command-line
run never pauses, whatever .env says). When on, a failure inside a step does not end the test: the test waits, with its
browser, its page and everything it saved earlier (DICT values, the login) still alive, until the person says what
to do. The same wait is used to stop *before* a step (debugPauseBefore: "all", or step numbers like "3,5"), to walk
a new test one step at a time. The web app and this process talk through files in <debugDir>:

    command.json      written by the web app: {"do": ..., "seq": N}
    paused.png        the page when the step failed

and through events on stdout (@@AUTOPLAY@@ lines, the way webapp/pytest_events.py does):
    {"event": "debug", "phase": "paused", "seq", "reason": "failure" | "step", "test", "step", "step_abs", "of", "kind",
     "label", "screen", "field", "action", "error", "url", "can_skip", "notice", "timeout_minutes", "deadline_in",
     "cdp_port", "has_picture"}
    {"event": "debug", "phase": "note", "seq", "info", "deadline_in"}      (something done while paused)
    {"event": "debug", "phase": "resumed", "seq", "do"}

What happens on each command (run_steps in tests/dynamic_test_suite.py does the re-running):
    retry    re-read the test case and the pages from disk, run the failed field (or the step) again, carry on
    restep   the same, from the step's first field
    skip     the same, leaving the failed field out (before a step: leaving the whole step out)
    go       before a step: run it and everything after it without stopping again
    abort    the failure stands (or the test ends): the test fails the usual way and the browser closes
and, without ending the pause:
    savestate  save the browser's state as the start point of this step (Run from step... can use it later)
    extend     wait another full period before giving up

Never waits forever: no command for debugTimeoutMinutes (default 30), the browser window closed, or the web app gone
(on systems where that can be seen) ends the pause as an abort.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PREFIX = "@@AUTOPLAY@@ "  # webapp/pytest_events.PREFIX; utils never imports the web app
POLL_SECONDS = 0.3
DEFAULT_TIMEOUT_MINUTES = 30
DEFAULT_FAST_FAIL_MS = 5000  # how long a debug run waits for a missing element before pausing
COMMANDS = ("retry", "restep", "skip", "go", "abort")  # these end a pause
SIDE_COMMANDS = ("savestate", "extend")  # these do something and keep waiting
ERROR_CHARS = 600

_PARENT = os.getppid()
_seq = 0
# Where the run is, for a failure to say which field it was: actions finished before the one that ran last, its
# field, and (inside a common action) which of its screens
progress: dict = {"completed": 0, "field": "", "type": "", "screen": "", "sub": None}
_resume: dict = {"sub": None, "actions": 0}  # what the next run of the failed step skips
_flags: dict = {"go": False, "skip_once": None}  # "go": stop pausing before steps; "skip_once": no stop before this step
_window = {"first": 1}  # the test's first step number in this run (a run from step 5 starts at 5)
_cdp = {"port": 0}  # the browser's remote debugging port, when it has one
summary: dict = {"pauses": 0, "retries": 0, "restarts": 0, "skips": 0, "aborts": 0, "saved": 0}


def enabled() -> bool:
    return str(os.getenv("debugOnFailure") or "").strip().lower() in ("true", "1", "yes") \
        and bool(os.getenv("debugDir"))


def _folder() -> Path:
    return Path(os.environ["debugDir"])


@dataclass
class Decision:
    steps: list  # the steps still to run, as they are on disk now (the failed one first)
    offset: int  # how many steps of the test's list come before them


# ───────────── small helpers the suite uses ─────────────

def set_window(first: int) -> None:
    _window["first"] = max(int(first or 1), 1)


def step_abs(step_no: int) -> int:
    """A step's number in the test case (step_no counts from the first step of this run)."""
    return _window["first"] + step_no - 1


def reserve_cdp_port() -> int:
    """A free local port for the browser's remote debugging, so the web app can look at the paused page."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        _cdp["port"] = s.getsockname()[1]
    return _cdp["port"]


def fast_ms(default_ms: int) -> int:
    """How long to wait for an element: a debug run gives up sooner (the pause lets the person decide), the rest don't."""
    if not enabled():
        return default_ms
    try:
        wanted = int(os.getenv("AUTOPLAY_DEBUG_FAST_FAIL_MS") or DEFAULT_FAST_FAIL_MS)
    except ValueError:
        wanted = DEFAULT_FAST_FAIL_MS
    return min(default_ms, max(wanted, 500))


def _take_pause_request() -> bool:
    """Was Pause pressed (webapp RunManager.request_pause)? The request is used up by the stop it asks for."""
    try:
        request = _folder() / "pause_request"
        if request.exists():
            request.unlink(missing_ok=True)
            return True
    except OSError:
        pass
    return False


def stop_before(step_no: int) -> bool:
    """Should the test wait before this step (debugPauseBefore: "all" or step numbers)?"""
    if not enabled():
        return False
    if _take_pause_request():  # the person pressed Pause on the Control page: stop before the step that is next
        return True
    if _flags["go"]:
        return False
    spec = str(os.getenv("debugPauseBefore") or "").strip().lower()
    if not spec:
        return False
    absolute = step_abs(step_no)
    hit = spec in ("all", "every") or str(absolute) in {part.strip() for part in spec.split(",")}
    if hit and _flags["skip_once"] == absolute:  # the person just said "run this step": do not ask again
        _flags["skip_once"] = None
        return False
    return hit


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


async def _save_state(page, test_id: str, step_abs_no: int) -> str:
    """Save the browser's state as the start point of this step; returns what to tell the person."""
    from utils import checkpoints
    if step_abs_no <= 1:
        return "Nothing to save: this is the first step, which starts from a fresh browser anyway."
    base = os.getenv("checkpointsDir")
    if not base:
        return "Could not save the state: no folder for it."
    path = await checkpoints.save_checkpoint(base, test_id, step_abs_no - 1, page)
    if path is None:
        return "Could not save the state of this browser."
    summary["saved"] += 1
    _write_summary()
    return (f"State saved: Run from step {step_abs_no} can start from the browser as it is now "
            "(with whatever this step already did).")


async def _wait_for_command(page, seq: int, minutes: float, clock: dict, test_id: str = "", step_abs_no: int = 0) -> str:
    """The person's command for pause number seq; 'abort' when nobody can answer any more."""
    command_file = _folder() / "command.json"
    while True:
        if command_file.exists():
            try:
                data = json.loads(command_file.read_text(encoding="utf-8"))
                command_file.unlink(missing_ok=True)
            except (OSError, ValueError):
                await asyncio.sleep(POLL_SECONDS)  # being written
                continue
            do = data.get("do")
            if data.get("seq") == seq and do in COMMANDS:
                return do
            if data.get("seq") == seq and do == "savestate":
                _emit(phase="note", seq=seq, info=await _save_state(page, test_id, step_abs_no),
                      deadline_in=max(clock["deadline"] - time.monotonic(), 0))
                continue
            if data.get("seq") == seq and do == "extend":
                clock["deadline"] = time.monotonic() + minutes * 60
                _emit(phase="note", seq=seq, info=f"Waiting another {minutes:g} minutes.", deadline_in=minutes * 60)
                continue
            logger.warning("[debug] ignored a stale or unknown command: %s", data)
        if time.monotonic() > clock["deadline"]:
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


async def _pause(*, reason: str, message: str, page, step_no: int, of: int, kind: str, label: str, where: dict,
                 reload_steps, test_id: str, can_skip: bool) -> tuple[str, list] | None:
    """Tell the app, wait for a command, read the files again. (command, steps as on disk) or None for abort."""
    global _seq
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "command.json").unlink(missing_ok=True)
    summary["pauses"] += 1
    notice = ""
    minutes = _timeout_minutes()
    while True:
        _seq += 1
        seq = _seq
        clock = {"deadline": time.monotonic() + minutes * 60}
        url = ""
        try:
            url = page.url
            await page.screenshot(path=str(folder / "paused.png"), timeout=5000)
        except Exception:  # noqa: BLE001 - a picture is a help, not a requirement
            logger.debug("[debug] no picture of the page", exc_info=True)
        field = where["field"]
        logger.warning("[debug] PAUSED at step %s (%s)%s: %s. The browser stays open.", step_no, label,
                       f", field '{field}'" if field else "", message)
        _emit(phase="paused", seq=seq, reason=reason, test=test_id, step=step_no, step_abs=step_abs(step_no), of=of,
              kind=kind, label=label, screen=where["screen"] or (label if kind == "page" else ""), field=field,
              action=where["type"], action_no=where["completed"] + 1, error=message, url=url, can_skip=can_skip,
              notice=notice, timeout_minutes=minutes, deadline_in=minutes * 60, cdp_port=_cdp["port"],
              has_picture=(folder / "paused.png").exists())
        _write_summary()
        do = await _wait_for_command(page, seq, minutes, clock, test_id, step_abs(step_no))
        _emit(phase="resumed", seq=seq, do=do)
        if do == "abort":
            summary["aborts"] += 1
            _write_summary()
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
                return do, list(fresh)
        logger.warning("[debug] %s", notice)


async def on_failure(error: BaseException, *, page, step_no: int, step: dict, of: int, kind: str, label: str,
                     reload_steps, test_id: str = "") -> Decision | None:
    """Pause on this failure. Returns what to run next, or None when the failure stands (abort)."""
    where = dict(progress)
    paused = await _pause(reason="failure", message=_first_line(error), page=page, step_no=step_no, of=of, kind=kind,
                          label=label, where=where, reload_steps=reload_steps, test_id=test_id,
                          can_skip=bool(where["field"]))
    if paused is None:
        try:
            error._autoplay_debugged = True  # the outer steps must not pause on the same failure again
        except Exception:  # noqa: BLE001
            pass
        return None
    do, fresh = paused
    sub = where["sub"]
    if do == "skip":
        _resume.update(sub=sub, actions=where["completed"] + 1)
        summary["skips"] += 1
    elif do == "restep":  # the whole step again
        _resume.update(sub=None, actions=0)
        summary["restarts"] += 1
    else:  # retry (go means nothing for a failure: the same)
        _resume.update(sub=sub, actions=where["completed"])
        summary["retries"] += 1
    _write_summary()
    logger.warning("[debug] RESUMING (%s) from step %s, in the same browser", do, step_no)
    return Decision(steps=fresh[step_no - 1:], offset=step_no - 1)


async def on_breakpoint(*, page, step_no: int, step: dict, of: int, kind: str, label: str, reload_steps,
                        test_id: str = "") -> Decision | None:
    """Wait before this step (debugPauseBefore). Returns what to run next, or None when the test is to end."""
    where = {"completed": 0, "field": "", "type": "", "screen": label if kind == "page" else "", "sub": None}
    paused = await _pause(reason="step", message=f"About to run step {step_no}: {label}", page=page, step_no=step_no,
                          of=of, kind=kind, label=label, where=where, reload_steps=reload_steps, test_id=test_id,
                          can_skip=True)
    if paused is None:
        return None
    do, fresh = paused
    clear_resume()
    if do == "skip":  # this step is left out
        summary["skips"] += 1
        _write_summary()
        logger.warning("[debug] SKIPPING step %s", step_no)
        return Decision(steps=fresh[step_no:], offset=step_no)
    if do == "go":
        _flags["go"] = True
    _flags["skip_once"] = step_abs(step_no)
    summary["retries"] += 1
    _write_summary()
    logger.warning("[debug] RUNNING step %s (%s)", step_no, do)
    return Decision(steps=fresh[step_no - 1:], offset=step_no - 1)
