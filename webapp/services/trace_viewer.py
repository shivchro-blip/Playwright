"""Open a run's Playwright trace in Playwright's own trace viewer, on the machine that hosts the app.

`playwright show-trace <zip>` opens a window on that machine, so this works only when the app listens on this
machine alone (the same rule as the recorder). On a shared server people download the trace instead.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from webapp.services.run_artifacts import SOURCE_RE

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def blocker(host: str) -> str:
    """Why the trace viewer can't open here ("" = it can)."""
    if host not in LOOPBACK:
        return ("The trace viewer opens a window on the machine running the app, so it only works when the app "
                "listens on this machine only (AUTOPLAY_HOST=127.0.0.1). Download the trace instead.")
    return ""


def open_trace(results_dir: Path, source: str, command: list[str]) -> Path:
    """Start the viewer for results_dir/<source> and return the trace path. ValueError says what is wrong."""
    source = (source or "").rsplit("/", 1)[-1]
    # Allure stores a trace (a zip) as <id>-attachment.zip or, when it does not know the type, .attach
    if not SOURCE_RE.match(source) or not source.endswith((".zip", ".attach")):
        raise ValueError("That is not a trace file of this run.")
    original = (Path(results_dir) / source).resolve()
    if Path(results_dir).resolve() not in original.parents or not original.is_file():
        raise ValueError("The trace file is not there any more.")
    folder = Path(results_dir).resolve().parent / "trace-viewer"  # inside the run folder: removed with the run
    folder.mkdir(exist_ok=True)
    path = folder / (original.stem + ".zip")  # the viewer wants a .zip name
    if not path.exists():
        shutil.copyfile(original, path)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" \
        else {"start_new_session": True}
    try:
        subprocess.Popen([*command, "show-trace", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **flags)
    except OSError as e:
        raise ValueError(f"Could not start the trace viewer: {e}") from e
    return path
