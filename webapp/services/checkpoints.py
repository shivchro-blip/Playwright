"""Saved browser states ("checkpoints") of earlier runs, for running a test case from a step.

The files are written by the suite (utils/checkpoints.py) under <run folder>/checkpoints/<test>/<step>.json.
This reads their headers, newest run first, so the Generator can offer "start from the state after step N".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from utils.checkpoints import safe_name


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    step: int  # the state after this step
    url: str
    saved: str
    path: Path


class CheckpointStore:
    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)

    def for_test(self, test_id: str) -> dict[int, Checkpoint]:
        """step -> the newest saved state after that step, across runs."""
        found: dict[int, Checkpoint] = {}
        if not self.runs_dir.is_dir():
            return found
        for run in sorted((p for p in self.runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
            folder = run / "checkpoints" / safe_name(test_id)
            for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
                if not file.stem.isdigit() or int(file.stem) in found:
                    continue
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if data.get("test_id") == test_id:
                    found[int(file.stem)] = Checkpoint(run.name, int(file.stem), str(data.get("url") or ""),
                                                       str(data.get("saved") or ""), file)
        return found
