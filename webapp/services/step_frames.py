"""Step time travel in the app: the pictures a run saved after each step (utils/step_capture.py), and how they
compare with the test's last passing run.

A frame is one step: its number and label, how it ended, how long it took, the page's address and title, and the
picture and HTML of the page after it. Comparing a run with the last run where the same test passed (same system,
environment and browser) lines the steps up by number and says where they part: a step that failed or was never
reached, ended on another page, or took much longer.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from utils.step_capture import folder_name

SLOWER = 2.0  # a step this many times slower than in the last pass is worth a note...
SLOWER_MIN_SECONDS = 2.0  # ...when it is also at least this many seconds slower


@dataclass
class Frame:
    n: int
    label: str
    kind: str
    status: str
    seconds: float
    url: str = ""
    title: str = ""
    error: str = ""
    image: str = ""  # URL of the picture, "" when none was saved
    html: str = ""  # URL of the page's HTML

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Difference:
    n: int
    label: str
    notes: list[str] = field(default_factory=list)
    now: Frame | None = None
    then: Frame | None = None

    def to_dict(self) -> dict:
        return {"n": self.n, "label": self.label, "notes": self.notes,
                "now": self.now.to_dict() if self.now else None, "then": self.then.to_dict() if self.then else None}


def frames(run, nodeid: str, runs_url: str = "/runs") -> list[Frame]:
    """The steps a run saved for one test (pytest node id, which names the browser too), in step order."""
    folder = Path(run.results_dir) / "steps" / folder_name(nodeid)
    if not folder.is_dir():
        return []
    base = f"{runs_url}/{run.id}/{Path(run.results_dir).name}/steps/{folder.name}"
    out = []
    for path in folder.glob("*.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            n = int(meta["n"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append(Frame(n, str(meta.get("label") or ""), str(meta.get("kind") or ""), str(meta.get("status") or ""),
                         float(meta.get("seconds") or 0), str(meta.get("url") or ""), str(meta.get("title") or ""),
                         str(meta.get("error") or ""), f"{base}/{n}.jpg" if meta.get("image") else "",
                         f"{base}/{n}.html" if meta.get("html") else ""))
    return sorted(out, key=lambda f: f.n)


def last_pass(runs, run, test_id: str, browser: str):
    """(run, nodeid) of the latest earlier finished run of the same system and environment where this test passed in
    this browser and saved its steps; None when there is none."""
    for other in runs:
        if other.id == run.id or not other.finished_at or other.created_at >= run.created_at:
            continue
        if other.config.system != run.config.system or other.config.env != run.config.env:
            continue
        for r in other.results.values():
            if r.test_id == test_id and r.browser == browser and r.outcome == "passed" and frames(other, r.nodeid):
                return other, r.nodeid
    return None


def _path(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/")


def compare(now: list[Frame], then: list[Frame]) -> list[Difference]:
    """Where this run's steps part from the last pass, step by step (steps that match are left out)."""
    by_then = {f.n: f for f in then}
    by_now = {f.n: f for f in now}
    out = []
    for n in sorted(set(by_then) | set(by_now)):
        a, b = by_now.get(n), by_then.get(n)
        notes = []
        if a is None:
            notes.append("not reached in this run")
        elif b is None:
            notes.append("not in the last pass")
        else:
            if a.status == "failed" and b.status != "failed":
                notes.append("failed here" + (f": {a.error}" if a.error else ""))
            if a.url and b.url and _path(a.url) != _path(b.url):
                notes.append(f"ended on another page: {_path(a.url)} (was {_path(b.url)})")
            elif a.title and b.title and a.title != b.title:
                notes.append(f"page title changed: {a.title!r} (was {b.title!r})")
            if a.seconds >= b.seconds * SLOWER and a.seconds - b.seconds >= SLOWER_MIN_SECONDS:
                notes.append(f"slower: {a.seconds:.1f} s (was {b.seconds:.1f} s)")
        if notes:
            out.append(Difference(n, (a or b).label, notes, a, b))
    return out


def timeline(svc, run, nodeid: str) -> dict:
    """Everything the viewer needs for one test of a run: its frames, the last pass's, and the differences."""
    result = run.results.get(nodeid)
    now = frames(run, nodeid)
    out = {"frames": [f.to_dict() for f in now], "last_pass": None, "then": [], "differences": []}
    if result is None or not now:
        return out
    found = last_pass(svc.runs.query(run.config.system, run.config.env, finished_only=True, limit=30), run,
                      result.test_id, result.browser)
    if found:
        other, other_node = found
        then = frames(other, other_node)
        out.update(last_pass=other.id, then=[f.to_dict() for f in then],
                   differences=[d.to_dict() for d in compare(now, then)])
    return out
