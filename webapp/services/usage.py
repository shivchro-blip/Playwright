"""Where pages and fields are used: the steps of test cases and of common actions.

A page belongs to a system (Pages/<SYSTEM>); a test case uses the pages of its own "system" key, or of the
folder it lives in. Common actions (Tests/com) are shared by every system, so a step there counts as a use of
the page with that name in whichever system runs it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from webapp.services.json_files import read_json
from webapp.services.test_case_service import TestCaseService, base_field


@dataclass(frozen=True)
class Source:
    path: Path
    kind: str  # "test" | "common"
    folder: str  # system folder of a test file; "com" for common actions
    rel: str  # path inside Tests/<folder>/MasterRegressionSuite (tests) or Tests/com (common)

    @property
    def label(self) -> str:
        return f"{self.folder}/{self.rel}" if self.kind == "test" else f"com/{self.rel}"


@dataclass(frozen=True)
class Use:
    source: str  # "test" | "common"
    file: str  # display path
    owner: str  # test case ID, or common action name
    step: int  # 1-based
    folder: str = ""  # system folder of the test file (to open it in the editor)
    rel: str = ""
    index: int = -1  # position of the test case in its file


@dataclass
class Usage:
    pages: dict[str, list[Use]] = field(default_factory=lambda: defaultdict(list))
    fields: dict[tuple[str, str], list[Use]] = field(default_factory=lambda: defaultdict(list))

    def page_uses(self, page: str) -> list[Use]:
        return list(self.pages.get(page, []))

    def field_uses(self, page: str, name: str) -> list[Use]:
        return list(self.fields.get((page, name), []))

    def page_count(self, page: str) -> int:
        """Distinct tests and common actions that use the page."""
        return len({(u.file, u.owner) for u in self.pages.get(page, [])})


def list_sources(tests: TestCaseService) -> list[Source]:
    sources: list[Source] = []
    for system in tests.systems():
        base = tests.suite_dir(system)
        sources += [Source(base / rel, "test", system, rel) for rel in tests.files(system)]
    com = tests.workspace / "Tests" / "com"
    if com.is_dir():
        sources += [Source(p, "common", "com", p.relative_to(com).as_posix()) for p in sorted(com.rglob("*.json"))]
    return sources


def iter_steps(data, source: Source) -> Iterator[tuple[str | None, str, int, int, dict]]:
    """(system the step runs in or None for common actions, owner, step number, test index, step)."""
    if not isinstance(data, dict):
        return
    if source.kind == "test":
        for index, case in enumerate(data.get("testCases") or []):
            if isinstance(case, dict):
                for n, step in enumerate(case.get("steps") or [], 1):
                    if isinstance(step, dict):
                        yield case.get("system") or source.folder, str(case.get("testCaseID", "")), n, index, step
    else:
        for name, action in (data.get("commonActions") or {}).items():
            for n, step in enumerate((action or {}).get("steps") or [] if isinstance(action, dict) else [], 1):
                if isinstance(step, dict):
                    yield None, str(name), n, -1, step


def step_refs(step: dict) -> list[tuple[str, str | None]]:
    """(page, field) pairs one step mentions; field None for the page itself. Includes `override` strings
    ("page|field|value") of common action steps."""
    refs: list[tuple[str, str | None]] = []
    screen = step.get("screen")
    if isinstance(screen, str) and screen:
        refs.append((screen, None))
        form = step.get("formData")
        for key in form if isinstance(form, dict) else []:
            refs.append((screen, base_field(str(key))))
    override = step.get("override")
    if isinstance(override, str) and override.count("|") >= 2:
        page, name, _ = override.split("|", 2)
        refs += [(page, None), (page, name)]
    return refs


class UsageIndex:
    def __init__(self, tests: TestCaseService):
        self.tests = tests

    def scan(self, system: str) -> Usage:
        usage = Usage()
        for source in list_sources(self.tests):
            try:
                data, _, _ = read_json(source.path)
            except (OSError, ValueError):
                continue
            for step_system, owner, n, index, step in iter_steps(data, source):
                if step_system is not None and step_system != system:
                    continue
                use = Use(source.kind, source.label, owner, n, source.folder, source.rel, index)
                for page, name in step_refs(step):
                    if name is None:
                        usage.pages[page].append(use)
                    else:
                        usage.fields[(page, name)].append(use)
        return usage
