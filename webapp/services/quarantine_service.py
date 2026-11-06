"""Quarantine for the app: who quarantined what is audited by the UI; the file format is utils/quarantine.py."""
from __future__ import annotations

from pathlib import Path

from utils import quarantine


class QuarantineService:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def all(self) -> dict[str, dict]:
        return quarantine.load(self.workspace)

    def is_quarantined(self, test_id: str) -> bool:
        return quarantine.base_id(test_id) in self.all()

    def add(self, test_id: str, reason: str = "") -> None:
        if not quarantine.base_id(test_id):
            raise ValueError("A test case ID is needed.")
        quarantine.add(self.workspace, test_id, reason)

    def remove(self, test_id: str) -> bool:
        return quarantine.remove(self.workspace, test_id)
