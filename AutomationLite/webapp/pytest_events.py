"""pytest plugin: emits machine-readable run events on stdout for the web app.

Loaded with `-p webapp.pytest_events`. Each event is one line:
    @@AUTOPLAY@@ {"event": "...", ...}
Only the xdist controller (or a plain serial run) emits; worker reports are relayed
to the controller, so events are not duplicated.
"""
from __future__ import annotations

import json
import sys

PREFIX = "@@AUTOPLAY@@ "
_enabled = True
_retries: dict[str, int] = {}  # nodeid -> times pytest-rerunfailures ran it again


def _emit(**payload) -> None:
    if not _enabled:
        return
    # sys.__stdout__ bypasses pytest capture (--capture=tee-sys / fd)
    out = sys.__stdout__ or sys.stdout
    out.write(PREFIX + json.dumps(payload, default=str) + "\n")
    out.flush()


def pytest_configure(config):
    global _enabled
    _enabled = not hasattr(config, "workerinput")


def pytest_collection_finish(session):
    _emit(event="collected", count=len(session.items))


def pytest_runtest_logstart(nodeid, location):
    _emit(event="start", nodeid=nodeid)


def pytest_runtest_logreport(report):
    # One final event per test: the call phase, or a setup/teardown failure/skip
    if report.when != "call" and report.passed:
        return
    if report.when == "teardown" and report.skipped:
        return
    outcome = report.outcome
    if outcome == "rerun":  # a failed attempt that will be run again: not the test's result
        _retries[report.nodeid] = _retries.get(report.nodeid, 0) + 1
        _emit(event="retry", nodeid=report.nodeid, attempt=_retries[report.nodeid])
        return
    if hasattr(report, "wasxfail"):
        outcome = "xfailed" if report.skipped else "xpassed"
    message = ""
    if report.failed or report.skipped:
        crash = getattr(report.longrepr, "reprcrash", None)
        if crash is not None and getattr(crash, "message", None):
            message = crash.message
        elif isinstance(report.longrepr, tuple) and len(report.longrepr) == 3:  # skip: (path, line, reason)
            message = str(report.longrepr[2])
        else:
            lines = (getattr(report, "longreprtext", "") or "").strip().splitlines()
            message = lines[-1] if lines else ""
        message = message.strip().splitlines()[0][:500] if message.strip() else ""
    if hasattr(report, "wasxfail"):  # xfailed / xpassed: lead with why (quarantine puts its reason here)
        reason = str(report.wasxfail or "").strip()
        message = (f"{reason} ({message})" if reason and message else reason or message)[:500]
    _emit(
        event="test",
        nodeid=report.nodeid,
        when=report.when,
        outcome=outcome,
        duration=round(getattr(report, "duration", 0.0) or 0.0, 2),
        message=message,
        retries=_retries.get(report.nodeid, 0),
    )


def pytest_sessionfinish(session, exitstatus):
    _emit(event="finished", exitstatus=int(exitstatus))
