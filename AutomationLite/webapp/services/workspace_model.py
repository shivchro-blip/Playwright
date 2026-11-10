"""The workspace model: one indexed picture of a workspace that every feature asks instead of reading files itself.

Before this module, "which steps does this test run, through its common actions and reused tests?" was answered in
five places (usage, journey coverage, change impact, refactor, common actions), each re-reading the JSON files on
every request. The model reads each workspace file once, re-reads only the files whose size or modification time
changed, and answers from indexes:

- tests per system (with their steps, tags, data set and file), common actions, page fields, data sets, journeys;
- where a page or field is used, directly (the step that names it) or transitively (every test that reaches it);
- each test's footprint: the pages, fields, common actions and reused tests it touches.

It does not change files and does not replace the services that edit them: it is the read side they share. The
answers are the same as the code it replaces (tests/unit/test_workspace_model.py proves it on several workspaces);
where the old code disagreed with itself, it follows the runner (page names resolve in the order the suite loads
them).
"""
from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from utils import page_components
from utils.steps_v2 import form_data
from webapp.services.json_files import WRITES, write_count
from webapp.services.test_case_service import base_field
from webapp.services.usage import Source, Usage, Use, iter_steps, list_sources, step_refs

SUITE_DIR = "MasterRegressionSuite"
DEPTH = 3  # how deep calls are followed (common actions, reused tests), like the runner


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _Files:
    """Parsed JSON per file, re-read only when the file's size or modification time changes."""

    def __init__(self):
        self._cache: dict[Path, tuple[tuple, object]] = {}
        self.reads = 0  # how many files were actually parsed (for tests and diagnostics)

    def get(self, path: Path):
        stamp = _stamp(path)
        if stamp is None:
            self._cache.pop(path, None)
            return None
        stamp = (*stamp, write_count(path))  # an app save always counts, even if size and time look the same
        cached = self._cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = None
        self.reads += 1
        self._cache[path] = (stamp, data)
        return data


@dataclass
class TestEntry:
    test_id: str
    system: str  # the case's own "system", else its folder (what the runner uses)
    folder: str  # system folder the file lives in
    rel: str  # file inside Tests/<folder>/MasterRegressionSuite
    index: int  # position in the file
    tags: list[str]
    steps: list[dict]
    data_set: str = ""


@dataclass
class Footprint:
    """Everything a test touches through the common actions and test cases it calls."""
    pages: set[str] = field(default_factory=set)
    fields: set[tuple[str, str]] = field(default_factory=set)
    common: set[str] = field(default_factory=set)
    tests: set[str] = field(default_factory=set)  # reused test cases


@dataclass
class Snapshot:
    """The workspace at one moment. Built by WorkspaceModel.snapshot(); treat as read-only."""
    sources: list[Source]
    data: dict[Path, object]  # parsed file per source path
    tests: dict[tuple[str, str], TestEntry]  # (system, test id)
    by_folder: dict[str, dict[str, list[dict]]]  # folder -> test id -> steps (last one in the folder wins)
    common: dict[str, list[dict]]  # name -> steps (the last file wins, like the suite)
    pages: dict[tuple[str, str], set[str]]  # (system, page) -> field names
    page_files: dict[tuple[str, str], str]  # (system, page) -> path inside Pages/<system>
    data_sets: list[str]
    journeys: list[dict]
    _usage: dict[str, Usage] = field(default_factory=dict)
    _footprints: dict[tuple[str, str], Footprint] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ───── direct uses (the Used by lists, rename, tidy) ─────

    def usage(self, system: str) -> Usage:
        """Where each page and field of a system is named, step by step (same as usage.UsageIndex.scan)."""
        with self._lock:
            if system in self._usage:
                return self._usage[system]
        usage = Usage()
        for source in self.sources:
            data = self.data.get(source.path)
            if data is None:
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
        with self._lock:
            self._usage[system] = usage
        return usage

    # ───── transitive (impact, coverage, "which tests reach this") ─────

    def footprint(self, system: str, test_id: str) -> Footprint:
        key = (system, test_id)
        with self._lock:
            if key in self._footprints:
                return self._footprints[key]
        fp = Footprint()
        node = self.tests.get(key)
        if node is not None:
            self._walk(node.system, node.steps, 0, fp)
        with self._lock:
            self._footprints[key] = fp
        return fp

    def _walk(self, system: str, steps: list[dict], depth: int, fp: Footprint) -> None:
        for step in steps:
            if isinstance(step.get("commonAction"), str):
                name = step["commonAction"]
                if name not in fp.common:
                    fp.common.add(name)
                    if depth < DEPTH:
                        self._walk(system, self.common.get(name, []), depth + 1, fp)
            if isinstance(step.get("testCase"), str):
                tid = step["testCase"]
                if tid not in fp.tests:
                    fp.tests.add(tid)
                    reused = self.tests.get((system, tid))
                    if reused and depth < DEPTH:
                        self._walk(system, reused.steps, depth + 1, fp)
            screen = step.get("screen")
            if isinstance(screen, str) and screen:
                fp.pages.add(screen)
                for key in form_data(step):  # classic formData or a v2 "do" list
                    fp.fields.add((screen, base_field(str(key))))
            override = step.get("override")
            if isinstance(override, str) and override.count("|") >= 2:
                page, name, _ = override.split("|", 2)
                fp.pages.add(page)
                fp.fields.add((page, name))

    def tests_reaching(self, system: str, *, page: str = "", field_name: str = "", common: str = "",
                       test: str = "", data_set: str = "") -> list[str]:
        """Test IDs of a system that reach a page, a field of a page, a common action, a reused test or a data
        set, directly or through what they call."""
        out = []
        for (sys_, tid), node in self.tests.items():
            if sys_ != system:
                continue
            fp = self.footprint(sys_, tid)
            if (page and not field_name and page in fp.pages) or (field_name and (page, field_name) in fp.fields) \
                    or (common and common in fp.common) or (test and (test == tid or test in fp.tests)) \
                    or (data_set and node.data_set == data_set):
                out.append(tid)
        return sorted(out, key=str.casefold)

    # ───── lookups ─────

    def systems(self) -> list[str]:
        return sorted({s for s, _ in self.tests} | {s for s, _ in self.pages}, key=str.casefold)

    def fields_of(self, system: str, page: str) -> set[str] | None:
        return self.pages.get((system, page))

    def steps_of(self, folder: str, test_id: str) -> list[dict] | None:
        """A test's steps by the folder it lives in (how journeys address tests)."""
        return self.by_folder.get(folder, {}).get(test_id)

    def search(self, text: str, limit: int = 50) -> list[dict]:
        """Tests, pages, fields, common actions, data sets and journeys whose name contains the text."""
        needle = (text or "").strip().casefold()
        if not needle:
            return []
        hits: list[dict] = []

        def add(kind: str, name: str, system: str = "", **extra) -> None:
            if needle in name.casefold():
                hits.append({"kind": kind, "name": name, "system": system, **extra})

        for (system, tid), node in sorted(self.tests.items()):
            add("test", tid, system, file=f"{node.folder}/{node.rel}")
        for (system, page), names in sorted(self.pages.items()):
            add("page", page, system, file=self.page_files.get((system, page), ""))
            for name in sorted(names):
                add("field", name, system, page=page)
        for name in sorted(self.common):
            add("common", name)
        for name in self.data_sets:
            add("data_set", name)
        for j in self.journeys:
            add("journey", str(j.get("name", "")), str(j.get("system", "")), id=str(j.get("id", "")))
        order = {"test": 0, "journey": 1, "page": 2, "field": 3, "common": 4, "data_set": 5}
        hits.sort(key=lambda h: (not h["name"].casefold().startswith(needle), order[h["kind"]], h["name"].casefold()))
        return hits[:limit]


class WorkspaceModel:
    """One per workspace. snapshot() is cheap when nothing changed: the files are only stat-ed."""

    def __init__(self, workspace: Path, tests_service=None):
        self.workspace = Path(workspace)
        if tests_service is None:
            from webapp.services.pom_service import PomService
            from webapp.services.test_case_service import TestCaseService
            tests_service = TestCaseService(self.workspace, PomService(self.workspace, self.workspace))
        self.tests_service = tests_service
        self.files = _Files()
        self._snapshot: Snapshot | None = None
        self._signature: tuple = ()
        self._lock = threading.Lock()

    def _page_paths(self) -> list[tuple[str, str, Path]]:
        """(system, screen name, path) in the order the suite resolves screens (os.walk; the last one wins)."""
        root = self.workspace / "Pages"
        out = []
        for system_dir in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
            for dirpath, _, filenames in os.walk(system_dir):
                for fn in filenames:
                    if fn.endswith(".json"):
                        out.append((system_dir.name, fn[:-5], Path(dirpath, fn)))
        return out

    def _other_paths(self) -> list[Path]:
        data_dir = self.workspace / "Data" / "TestData"
        sets = sorted(data_dir.glob("*.json")) if data_dir.is_dir() else []
        return sets + [self.workspace / "Data" / "journeys.json"]

    def snapshot(self) -> Snapshot:
        sources = list_sources(self.tests_service)
        pages = self._page_paths()
        others = self._other_paths()
        signature = (WRITES[0],) + tuple((str(p), _stamp(p))
                                          for p in [s.path for s in sources] + [p for _, _, p in pages] + others)
        with self._lock:
            if self._snapshot is not None and signature == self._signature:
                return self._snapshot
            snap = self._build(sources, pages, others)
            self._snapshot, self._signature = snap, signature
            return snap

    def _build(self, sources: list[Source], page_paths, others: list[Path]) -> Snapshot:
        data = {s.path: self.files.get(s.path) for s in sources}
        tests: dict[tuple[str, str], TestEntry] = {}
        by_folder: dict[str, dict[str, list[dict]]] = defaultdict(dict)
        common: dict[str, list[dict]] = {}
        for source in sources:
            doc = data.get(source.path)
            if not isinstance(doc, dict):
                continue
            if source.kind == "test":
                for index, case in enumerate(doc.get("testCases") or []):
                    if not isinstance(case, dict) or not case.get("testCaseID"):
                        continue
                    tid = str(case["testCaseID"])
                    steps = [s for s in case.get("steps") or [] if isinstance(s, dict)]
                    tags = case.get("tags") or []
                    tests[(str(case.get("system") or source.folder), tid)] = TestEntry(
                        tid, str(case.get("system") or source.folder), source.folder, source.rel, index,
                        [str(t) for t in tags] if isinstance(tags, list) else [str(tags)], steps,
                        str(case.get("dataSet") or ""))
                    by_folder[source.folder][tid] = list(case.get("steps") or [])
            else:
                for name, action in (doc.get("commonActions") or {}).items():
                    common[str(name)] = [s for s in (action or {}).get("steps") or [] if isinstance(s, dict)] \
                        if isinstance(action, dict) else []
        pages: dict[tuple[str, str], set[str]] = {}
        page_files: dict[tuple[str, str], str] = {}
        docs: dict[tuple[str, str], object] = {}
        for system, name, path in page_paths:
            docs[(system, name)] = self.files.get(path)
            page_files[(system, name)] = path.relative_to(self.workspace / "Pages" / system).as_posix()
        for (system, name), doc in docs.items():
            try:  # a page's fields include those of the shared components it uses (utils/page_components.py)
                fields = page_components.expand_fields(doc, lambda n, s=system: docs.get((s, n))) \
                    if isinstance(doc, dict) else None
            except page_components.ComponentError:
                fields = doc.get("fields")
            pages[(system, name)] = set(fields) if isinstance(fields, dict) else set()
        data_sets = sorted(p.stem for p in others if p.parent.name == "TestData" and p.exists())
        journeys_doc = self.files.get(self.workspace / "Data" / "journeys.json")
        journeys = [j for j in (journeys_doc or {}).get("journeys", []) if isinstance(j, dict)] \
            if isinstance(journeys_doc, dict) else []
        return Snapshot(sources, data, tests, dict(by_folder), common, pages, page_files, data_sets, journeys)


_models: dict[str, WorkspaceModel] = {}
_models_lock = threading.Lock()


def model_for(workspace: Path, tests_service=None) -> WorkspaceModel:
    """The shared model of a workspace (one per process), so every caller benefits from the same cache."""
    key = str(Path(workspace).resolve())
    with _models_lock:
        model = _models.get(key)
        if model is None:
            model = _models[key] = WorkspaceModel(workspace, tests_service)
        return model
