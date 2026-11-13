"""Pause on failure (utils/debug_pause.py): who may pause, what is resumed where, and when a pause ends."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from utils import debug_pause as dp


class FakePage:
    url = "http://site/page"

    def __init__(self):
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def screenshot(self, path: str, timeout: int = 0):
        Path(path).write_bytes(b"png")


@pytest.fixture
def pause_env(tmp_path, monkeypatch):
    monkeypatch.setenv("debugOnFailure", "true")
    monkeypatch.setenv("debugDir", str(tmp_path / "debug"))
    monkeypatch.setattr(dp, "POLL_SECONDS", 0.01)
    events: list[dict] = []
    monkeypatch.setattr(dp, "_emit", lambda **payload: events.append(payload))
    dp.begin_step()
    dp.clear_resume()
    dp.summary.update(pauses=0, retries=0, restarts=0, skips=0, aborts=0)
    return tmp_path / "debug", events


def operator(folder: Path, events: list[dict], *answers):
    """The web app: answer each pause in turn with a command."""
    already = len([e for e in events if e["phase"] == "paused"])  # pauses of earlier calls in the same test

    async def run():
        for n, do in enumerate(answers, 1):
            while len([e for e in events if e["phase"] == "paused"]) < already + n:
                await asyncio.sleep(0.01)
            seq = [e for e in events if e["phase"] == "paused"][-1]["seq"]
            (folder / "command.json").write_text(json.dumps({"do": do, "seq": seq}), encoding="utf-8")
    return run()


def failure():
    return AssertionError("Failure: fill on Element 'missing' failed")


async def pause_once(folder, events, *answers, reload=None, page=None, step_no=2):
    fresh = [{"screen": f"S{i}"} for i in range(1, 5)]
    page = page or FakePage()
    err = failure()
    decision, _ = await asyncio.gather(
        dp.on_failure(err, page=page, step_no=step_no, step={"screen": "S2"}, of=4, kind="page", label="S2",
                      reload_steps=reload or (lambda: fresh), test_id="TC-1"),
        operator(folder, events, *answers))
    return decision, err, fresh


def test_it_never_pauses_unless_the_run_asked_and_the_app_gave_a_folder(monkeypatch, tmp_path):
    for flag, folder in (("true", None), (None, str(tmp_path)), ("false", str(tmp_path))):
        monkeypatch.delenv("debugOnFailure", raising=False)
        monkeypatch.delenv("debugDir", raising=False)
        if flag:
            monkeypatch.setenv("debugOnFailure", flag)
        if folder:
            monkeypatch.setenv("debugDir", folder)
        assert not dp.enabled() and not dp.pausable(failure())
    monkeypatch.setenv("debugOnFailure", "true")
    monkeypatch.setenv("debugDir", str(tmp_path))
    assert dp.pausable(failure())
    assert not dp.pausable(KeyboardInterrupt()) and not dp.pausable(asyncio.CancelledError())
    import pytest as _pytest
    try:
        _pytest.skip("x")
    except BaseException as skipped:  # noqa: BLE001 - a skip is not a failure to debug
        assert not dp.pausable(skipped)
    try:
        _pytest.fail("x")
    except BaseException as failed:  # noqa: BLE001
        assert dp.pausable(failed)  # a failed check is what pause is for


def test_the_resume_bookkeeping_is_one_shot(pause_env):
    dp.note_action(2, {"field": "city", "type": "fill"})
    assert dp.progress["completed"] == 2 and dp.progress["field"] == "city"
    assert dp.take_action_resume() == 0
    dp._resume.update(actions=3, sub=(0, 2))
    assert dp.take_action_resume() == 3 and dp.take_action_resume() == 0
    assert [dp.skip_sub(0, i) for i in range(4)] == [True, True, False, False]  # up to the failed screen, then on
    assert dp.skip_sub(0, 0) is False  # used up


def test_retry_goes_on_from_the_failed_field_with_the_steps_as_they_are_now(pause_env):
    folder, events = pause_env
    dp.begin_screen("S2")
    dp.note_action(1, {"field": "missing", "type": "fill"})
    decision, err, fresh = asyncio.run(pause_once(folder, events, "retry"))
    assert [s["screen"] for s in decision.steps] == ["S2", "S3", "S4"] and decision.offset == 1
    assert dp.take_action_resume() == 1  # the first field already ran: the failed second one runs again
    paused = events[0]
    assert (paused["phase"], paused["test"], paused["step"], paused["of"], paused["field"], paused["can_skip"]) == \
        ("paused", "TC-1", 2, 4, "missing", True)
    assert paused["screen"] == "S2" and "missing" in paused["error"] and paused["has_picture"]
    assert (folder / "paused.png").read_bytes() == b"png"
    assert events[-1]["phase"] == "resumed" and events[-1]["do"] == "retry"
    assert not getattr(err, "_autoplay_debugged", False)
    assert json.loads((folder / "summary.json").read_text())["retries"] == 1


def test_skip_leaves_the_field_out_and_restep_starts_the_step_over(pause_env):
    folder, events = pause_env
    dp.begin_screen("S2")
    dp.note_action(1, {"field": "missing", "type": "fill"})
    asyncio.run(pause_once(folder, events, "skip"))
    assert dp.take_action_resume() == 2
    dp.begin_screen("S2")
    dp.note_action(1, {"field": "missing", "type": "fill"})
    asyncio.run(pause_once(folder, events, "restep"))
    assert dp.take_action_resume() == 0 and dp.summary["skips"] == 1 and dp.summary["restarts"] == 1


def test_inside_a_common_action_the_resume_point_is_the_screen_that_failed(pause_env):
    folder, events = pause_env
    dp.begin_screen("S2")
    dp.note_sub(0, 2)
    dp.note_action(0, {"field": "zip", "type": "fill"})
    asyncio.run(pause_once(folder, events, "retry"))
    assert [dp.skip_sub(0, i) for i in range(4)] == [True, True, False, False]
    assert dp.take_action_resume() == 0


def test_abort_lets_the_failure_stand_and_marks_it_so_outer_steps_do_not_pause_again(pause_env):
    folder, events = pause_env
    decision, err, _ = asyncio.run(pause_once(folder, events, "abort"))
    assert decision is None and err._autoplay_debugged is True and not dp.pausable(err)
    assert dp.summary["aborts"] == 1


def test_a_failure_with_no_field_cannot_be_skipped(pause_env):
    folder, events = pause_env
    dp.begin_screen("S2")  # nothing ran: the page was not found
    asyncio.run(pause_once(folder, events, "retry"))
    assert events[0]["can_skip"] is False and events[0]["field"] == ""


def test_files_that_cannot_be_read_are_said_and_the_pause_stays_until_they_are_fixed(pause_env):
    folder, events = pause_env
    calls = []

    def reload():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("Expecting ',' delimiter: line 4")
        return [{"screen": f"S{i}"} for i in range(1, 5)]

    decision, _, _ = asyncio.run(pause_once(folder, events, "retry", "retry", reload=reload))
    paused = [e for e in events if e["phase"] == "paused"]
    assert len(paused) == 2 and paused[0]["notice"] == "" and "Expecting ','" in paused[1]["notice"]
    assert paused[1]["seq"] > paused[0]["seq"] and decision is not None


def test_a_test_that_lost_its_failed_step_cannot_be_resumed(pause_env):
    folder, events = pause_env
    short = [{"screen": "S1"}]
    decision, _, _ = asyncio.run(pause_once(folder, events, "retry", "abort", reload=lambda: short))
    assert "step 2" in [e for e in events if e["phase"] == "paused"][1]["notice"] and decision is None


def test_a_stale_command_is_ignored(pause_env):
    folder, events = pause_env

    async def run():
        task = asyncio.create_task(dp.on_failure(failure(), page=FakePage(), step_no=1, step={}, of=1, kind="page",
                                                 label="S1", reload_steps=lambda: [{}], test_id="T"))
        while not events:
            await asyncio.sleep(0.01)
        (folder / "command.json").write_text(json.dumps({"do": "retry", "seq": events[0]["seq"] - 5}))
        await asyncio.sleep(0.1)
        assert not task.done()  # a command for an earlier pause does nothing
        (folder / "command.json").write_text(json.dumps({"do": "abort", "seq": events[0]["seq"]}))
        return await task

    assert asyncio.run(run()) is None


def test_a_pause_ends_when_nobody_answers_or_the_page_is_closed(pause_env, monkeypatch):
    folder, events = pause_env
    monkeypatch.setattr(dp, "_timeout_minutes", lambda: 0.005)
    decision, _, _ = asyncio.run(pause_once(folder, events))  # no operator answer: the timeout ends it
    assert decision is None
    monkeypatch.setattr(dp, "_timeout_minutes", lambda: 5)
    page = FakePage()
    page.closed = True
    decision, _, _ = asyncio.run(pause_once(folder, events, page=page))
    assert decision is None


# ───────────── stepping through, signing in, saving state, waiting longer, fast fail ─────────────

async def breakpoint_once(folder, events, *answers, reload=None, step_no=2, **kwargs):
    fresh = [{"screen": f"S{i}"} for i in range(1, 5)]
    decision, _ = await asyncio.gather(
        dp.on_breakpoint(page=FakePage(), step_no=step_no, step={"screen": "S2"}, of=4, kind="page", label="S2",
                         reload_steps=reload or (lambda: fresh), test_id="TC-1"),
        operator(folder, events, *answers))
    return decision, fresh


def test_stop_before_follows_the_option_and_one_run_this_step_does_not_ask_twice(pause_env, monkeypatch):
    monkeypatch.delenv("debugPauseBefore", raising=False)
    assert dp.stop_before(1) is False  # not asked for: never
    monkeypatch.setenv("debugPauseBefore", "2, 4")
    dp.set_window(1)
    assert [dp.stop_before(n) for n in (1, 2, 3, 4)] == [False, True, False, True]
    monkeypatch.setenv("debugPauseBefore", "all")
    assert dp.stop_before(1) and dp.stop_before(7)
    dp._flags["skip_once"] = dp.step_abs(3)  # "run this step": the next ask for it is waved through, once
    assert dp.stop_before(3) is False and dp.stop_before(3) is True
    dp.set_window(5)  # a run from step 5: its first step is number 5 in the test case
    monkeypatch.setenv("debugPauseBefore", "5")
    assert dp.stop_before(1) is True and dp.step_abs(2) == 6
    dp.set_window(1)


def test_a_run_that_never_asked_never_stops_before_a_step(monkeypatch, tmp_path):
    monkeypatch.setenv("debugPauseBefore", "all")
    monkeypatch.delenv("debugOnFailure", raising=False)
    monkeypatch.setenv("debugDir", str(tmp_path))
    assert dp.stop_before(1) is False  # pause on failure is off


def test_before_a_step_run_it_go_on_or_skip_it(pause_env, monkeypatch):
    monkeypatch.setenv("debugPauseBefore", "all")
    folder, events = pause_env
    dp._flags.update(go=False, skip_once=None)
    decision, _ = asyncio.run(breakpoint_once(folder, events, "retry"))
    assert [s["screen"] for s in decision.steps] == ["S2", "S3", "S4"] and decision.offset == 1
    paused = events[0]
    assert paused["reason"] == "step" and paused["can_skip"] is True and paused["field"] == ""
    assert paused["error"] == "About to run step 2: S2" and paused["step_abs"] == 2
    assert dp.stop_before(2) is False  # it is the step they just chose to run
    assert dp.stop_before(3) is True  # and the next one asks again

    decision, _ = asyncio.run(breakpoint_once(folder, events, "skip", step_no=3))
    assert [s["screen"] for s in decision.steps] == ["S4"] and decision.offset == 3 and dp.summary["skips"] == 1

    decision, _ = asyncio.run(breakpoint_once(folder, events, "go"))
    assert decision.steps and dp.stop_before(3) is False and dp.stop_before(4) is False  # no more stops
    dp._flags.update(go=False, skip_once=None)

    decision, _ = asyncio.run(breakpoint_once(folder, events, "abort"))
    assert decision is None


def test_signing_in_can_pause_and_be_tried_again(pause_env):
    folder, events = pause_env

    async def run():
        decision, _ = await asyncio.gather(
            dp.on_failure(failure(), page=FakePage(), step_no=1, step={}, of=3, kind="login", label="Sign-in",
                          reload_steps=lambda: [{"screen": "A"}, {"screen": "B"}, {"screen": "C"}], test_id="TC-1"),
            operator(folder, events, "retry"))
        return decision

    decision = asyncio.run(run())
    assert len(decision.steps) == 3 and decision.offset == 0  # the whole test again, signing in first
    assert events[0]["kind"] == "login" and events[0]["label"] == "Sign-in" and events[0]["can_skip"] is False
    assert dp.take_action_resume() == 0


def test_save_state_and_keep_waiting_do_not_end_the_pause(pause_env, monkeypatch):
    folder, events = pause_env
    saved = []

    async def fake_save(base, test_id, step, page):
        saved.append((base, test_id, step))
        return Path(base) / "x.json"

    monkeypatch.setenv("checkpointsDir", str(folder.parent / "checkpoints"))
    monkeypatch.setattr("utils.checkpoints.save_checkpoint", fake_save)

    async def run():
        task = asyncio.create_task(dp.on_failure(failure(), page=FakePage(), step_no=3, step={}, of=4, kind="page",
                                                 label="S3", reload_steps=lambda: [{}, {}, {}, {}], test_id="TC-1"))
        while not events:
            await asyncio.sleep(0.01)
        seq = events[0]["seq"]
        for do in ("savestate", "extend"):
            (folder / "command.json").write_text(json.dumps({"do": do, "seq": seq}))
            before = len(events)
            while len(events) == before:
                await asyncio.sleep(0.01)
            assert not task.done()  # still waiting after each
        (folder / "command.json").write_text(json.dumps({"do": "retry", "seq": seq}))
        return await task

    assert asyncio.run(run()) is not None
    notes = [e for e in events if e["phase"] == "note"]
    # the state after step 2: step 3 can start from it
    assert saved == [(str(folder.parent / "checkpoints"), "TC-1", 2)]
    assert "Run from step 3" in notes[0]["info"] and "another" in notes[1]["info"] and notes[1]["deadline_in"] > 0
    assert events[0]["deadline_in"] > 0 and events[0]["timeout_minutes"] > 0


def test_nothing_is_saved_before_the_first_step(pause_env, monkeypatch):
    folder, events = pause_env
    monkeypatch.setenv("checkpointsDir", str(folder.parent / "checkpoints"))
    assert "first step" in asyncio.run(dp._save_state(FakePage(), "TC-1", 1))


def test_a_debug_run_gives_up_on_a_missing_element_sooner_and_other_runs_do_not(monkeypatch, tmp_path):
    monkeypatch.delenv("debugOnFailure", raising=False)
    monkeypatch.delenv("debugDir", raising=False)
    assert dp.fast_ms(30000) == 30000
    monkeypatch.setenv("debugOnFailure", "true")
    monkeypatch.setenv("debugDir", str(tmp_path))
    assert dp.fast_ms(30000) == dp.DEFAULT_FAST_FAIL_MS and dp.fast_ms(100) == 100  # never longer than asked
    monkeypatch.setenv("AUTOPLAY_DEBUG_FAST_FAIL_MS", "1200")
    assert dp.fast_ms(30000) == 1200
    monkeypatch.setenv("AUTOPLAY_DEBUG_FAST_FAIL_MS", "5")
    assert dp.fast_ms(30000) == 500  # and not so short that a page that is still loading fails
    monkeypatch.setenv("AUTOPLAY_DEBUG_FAST_FAIL_MS", "soon")
    assert dp.fast_ms(30000) == dp.DEFAULT_FAST_FAIL_MS


def test_a_free_local_port_is_reserved_for_the_browser():
    port = dp.reserve_cdp_port()
    assert 1024 < port < 65536 and dp._cdp["port"] == port


def test_a_pause_request_stops_before_the_next_step_once_even_after_run_to_the_end(pause_env):
    folder, _events = pause_env
    dp._flags.update(go=True, skip_once=None)
    try:
        assert not dp.stop_before(3)  # nothing asked
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pause_request").write_text("1")
        assert dp.stop_before(3)  # Pause was pressed
        assert not (folder / "pause_request").exists() and not dp.stop_before(4)  # used up
    finally:
        dp._flags.update(go=False, skip_once=None)


def test_a_pause_request_is_ignored_when_pausing_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("debugOnFailure", "false")
    monkeypatch.setenv("debugDir", str(tmp_path))
    (tmp_path / "pause_request").write_text("1")
    assert not dp.stop_before(1)
