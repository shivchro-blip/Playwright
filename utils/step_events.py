"""Step events: what each step of a test did, written while the test runs, for the web app to show live.

One JSON line per event in <reportsDir>/autoplay-steps-<pid>.jsonl (one file per pytest worker, so parallel runs
work: worker output never reaches the app, files do). Events:
    {"event": "step", "phase": "start", "nodeid", "test", "n", "of", "kind", "label", "at"}
    {"event": "step", "phase": "end", ..., "status": "passed" | "failed", "seconds", "error"}
`n` is the step's number in the test case (a run from step 5 starts at 5). Only a test's own steps are reported;
the steps inside a common action or a reused test case belong to the step that calls them.
Writing never fails a test: an unwritable folder just means no live steps.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)
ERROR_CHARS = 300


def describe(step: dict) -> tuple[str, str]:
    """(kind, label) of a test's step, as a person would say it."""
    if "commonAction" in step:
        return "common", str(step.get("commonAction"))
    if "testCase" in step:
        return "test", str(step.get("testCase"))
    if "API_REQUEST" in step:
        return "api", str(step.get("API_REQUEST"))
    return "page", str(step.get("screen", ""))


def _write(record: dict) -> None:
    folder = os.getenv("reportsDir")
    if not folder:
        return
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        with (Path(folder) / f"autoplay-steps-{os.getpid()}.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        logger.debug("[steps] could not write a step event", exc_info=True)


class StepReporter:
    """Handed to run_steps for a test's own steps: start() before each, end() after it (or as it fails)."""

    def __init__(self, nodeid: str, test_id: str, first: int, total: int):
        self.nodeid, self.test_id, self.first, self.total = nodeid, test_id, first, total
        self._started: dict[int, float] = {}

    def _base(self, step_no: int, step: dict) -> dict:
        kind, label = describe(step)
        return {"event": "step", "nodeid": self.nodeid, "test": self.test_id, "n": self.first + step_no - 1,
                "of": self.total, "kind": kind, "label": label, "at": time.time()}

    def start(self, step_no: int, step: dict) -> None:
        self._started[step_no] = time.monotonic()
        _write({**self._base(step_no, step), "phase": "start"})

    def end(self, step_no: int, step: dict, error: BaseException | None = None) -> dict:
        """Write the step's end; returns the event (n, kind, label, status, seconds, error)."""
        seconds = round(time.monotonic() - self._started.pop(step_no, time.monotonic()), 2)
        message = ""
        if error is not None:
            message = str(getattr(error, "msg", "") or error).strip().splitlines()
            message = message[0][:ERROR_CHARS] if message else type(error).__name__
        record = {**self._base(step_no, step), "phase": "end", "status": "failed" if error else "passed",
                  "seconds": seconds, "error": message}
        _write(record)
        return record
