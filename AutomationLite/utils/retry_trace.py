"""Trace a retried test: when a failed test is run again (retryFailed), record a Playwright trace for the retry
even if the run did not ask for traces, so the attempt that shows the problem has one.
"""
from __future__ import annotations

import os

ACTIVE = False  # set by the suite for the length of a retried test


def tracing_on() -> bool:
    return ACTIVE or os.getenv("tracing", "false").lower() == "true"
