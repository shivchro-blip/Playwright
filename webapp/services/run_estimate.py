"""How long a set of test cases will take, from how long they took in earlier runs. Plain functions, unit-tested."""
from __future__ import annotations

import statistics
from typing import Iterable


def format_duration(seconds: float) -> str:
    """'45 s', '12 min', '2 h 10 min'."""
    seconds = max(int(round(seconds)), 0)
    if seconds < 90:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def estimate(runs: Iterable, system: str, env: str, test_ids: Iterable[str]) -> tuple[float, int, int] | None:
    """(seconds for one pass over the tests, how many of them have a past duration, how many there are).

    A test's time is the median of its finished results on this system and environment; a test with no history is
    given the median of the ones that have, so the total is a fair guess rather than a lower bound. None when no
    test of the set has ever run here."""
    ids = list(dict.fromkeys(test_ids))
    wanted = set(ids)
    durations: dict[str, list[float]] = {}
    for run in runs:
        config = getattr(run, "config", None)
        if getattr(config, "system", None) != system or getattr(config, "env", None) != env:
            continue
        for result in getattr(run, "results", {}).values():
            if result.test_id in wanted and result.outcome in ("passed", "failed") and result.duration > 0:
                durations.setdefault(result.test_id, []).append(float(result.duration))
    known = {tid: statistics.median(ds) for tid, ds in durations.items()}
    if not known:
        return None
    typical = statistics.median(known.values())
    return sum(known.get(tid, typical) for tid in ids), len(known), len(ids)
