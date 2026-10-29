"""Lists test cases the way utils/test_data_loader selects them, without importing the suite."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from webapp.services.json_files import NAME_RE


@dataclass
class TestCaseInfo:
    test_id: str
    tags: list[str]
    file: str  # relative to the system's MasterRegressionSuite folder
    steps: int
    duplicate: bool = False


@dataclass
class Catalog:
    system: str
    tests: list[TestCaseInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def all_tags(self) -> list[str]:
        return sorted({t for tc in self.tests for t in tc.tags}, key=str.casefold)

    def select(self, tags: list[str]) -> list[TestCaseInfo]:
        """Same rule as load_test_cases_from_folder: case-insensitive tag match, no tags -> nothing."""
        wanted = {t.strip().casefold() for t in tags if t and t.strip()}
        return [tc for tc in self.tests if tc.steps and any(t.casefold() in wanted for t in tc.tags)]


class TestCatalogService:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def systems_with_tests(self) -> list[str]:
        root = self.workspace / "Tests"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if (p / "MasterRegressionSuite").is_dir())

    def load(self, system: str | None) -> Catalog:
        catalog = Catalog(system=system or "")
        if not system or not NAME_RE.match(system) or system in (".", ".."):
            return catalog
        suite_dir = self.workspace / "Tests" / system / "MasterRegressionSuite"
        if not suite_dir.is_dir():
            return catalog

        for path in sorted(suite_dir.rglob("*.json")):
            rel = path.relative_to(suite_dir).as_posix()
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as e:
                catalog.errors.append(f"{rel}: {e}")
                continue
            for tc in data.get("testCases", []) if isinstance(data, dict) else []:
                catalog.tests.append(TestCaseInfo(
                    test_id=str(tc.get("testCaseID", "<no id>")),
                    tags=[str(t).strip() for t in tc.get("tags", []) if str(t).strip()],
                    file=rel,
                    steps=len(tc.get("steps") or []),
                ))

        counts = Counter(tc.test_id for tc in catalog.tests)
        for tc in catalog.tests:
            tc.duplicate = counts[tc.test_id] > 1
        return catalog
