"""Changes that touch a page and every test that uses it: rename, merge duplicates, remove, tidy.

Everything is computed in memory first (a Plan: the files that would change and the renames), shown to the
user, and only then applied: every file's hash is re-checked, every write is atomic, and if anything fails the
files already written are put back, so a refactor either happens completely or not at all.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from webapp.services.json_files import ConflictError, current_hash, read_json, safe_path, write_json
from webapp.services.pom_service import PomService
from webapp.services.test_case_service import TestCaseService
from webapp.services.usage import Source, iter_steps, list_sources

CLICK_TYPES = {"button", "link", "checkbox", "radio", "table"}
NAME_OK = re.compile(r"^[\w\-.]+$")
PAGE_NAME_OK = re.compile(r"^[A-Za-z0-9_\-]+$")


class RefactorError(ValueError):
    """Shown to the user as is."""


@dataclass
class Doc:
    path: Path
    label: str
    data: Any
    sha: str
    indent: str
    changes: int = 0


@dataclass
class Plan:
    docs: list[Doc] = field(default_factory=list)  # files to rewrite
    renames: list[tuple[Path, Path]] = field(default_factory=list)  # (old, new) page files

    @property
    def empty(self) -> bool:
        return not self.docs and not self.renames

    def summary(self) -> list[str]:
        lines = [f"Rename {old.name} to {new.name}" for old, new in self.renames]
        lines += [f"{d.label}: {d.changes} change{'' if d.changes == 1 else 's'}" for d in self.docs]
        return lines


def _split(key: str) -> tuple[str, str]:
    name, bar, rest = key.partition("|")
    return name, bar + rest


class Refactor:
    def __init__(self, pom: PomService, tests: TestCaseService):
        self.pom = pom
        self.tests = tests
        self._docs: dict[Path, Doc] = {}
        self._renames: list[tuple[Path, Path]] = []
        self._sources: list[Source] | None = None

    # ───────────── loading ─────────────

    def _doc(self, path: Path, label: str) -> Doc:
        if path not in self._docs:
            data, sha, indent = read_json(path)
            self._docs[path] = Doc(path, label, data, sha, indent)
        return self._docs[path]

    def _page(self, system: str, page: str) -> Doc:
        index = self.pom.screen_index(system)
        if page not in index:
            raise RefactorError(f"No page named {page!r} in {system}.")
        if sum(1 for r in self.pom.list_pages(system) if r.name == page) > 1:
            raise RefactorError(f"Two pages are named {page!r}; give them different names first.")
        return self._doc(safe_path(self.pom.pages_dir(system), index[page]), f"Pages/{system}/{index[page]}")

    def _steps(self, system: str) -> Iterator[tuple[Doc, dict]]:
        """Every step that runs in the system: its tests', and the common actions' (shared by all systems)."""
        if self._sources is None:
            self._sources = list_sources(self.tests)
        for source in self._sources:
            try:
                doc = self._doc(source.path, source.label)
            except (OSError, ValueError):
                continue
            for step_system, _, _, _, step in iter_steps(doc.data, source):
                if step_system is None or step_system == system:
                    yield doc, step

    @staticmethod
    def _rename_override(step: dict, page: str, field_old: str | None, new_page: str | None,
                         new_field: str | None) -> bool:
        override = step.get("override")
        if not isinstance(override, str) or override.count("|") < 2:
            return False
        p, f, rest = override.split("|", 2)
        if p != page or (field_old is not None and f != field_old):
            return False
        step["override"] = f"{new_page or p}|{new_field or f}|{rest}"
        return True

    # ───────────── operations ─────────────

    def rename_page(self, system: str, old: str, new: str) -> None:
        new = new.strip().removesuffix(".json")
        if not PAGE_NAME_OK.match(new):
            raise RefactorError("Page name: letters, digits, '_' or '-' only.")
        if new == old:
            raise RefactorError("That is already the page's name.")
        if new in self.pom.screen_index(system):
            raise RefactorError(f"A page named {new!r} already exists in {system}.")
        doc = self._page(system, old)
        new_path = doc.path.with_name(new + ".json")
        if new_path.exists():
            raise RefactorError(f"{new_path.name} already exists.")
        for step_doc, step in self._steps(system):
            if step.get("screen") == old:
                step["screen"] = new
                step_doc.changes += 1
            if self._rename_override(step, old, None, new, None):
                step_doc.changes += 1
        self._renames.append((doc.path, new_path))

    def rename_field(self, system: str, page: str, old: str, new: str) -> None:
        new = new.strip()
        if not NAME_OK.match(new) or "|" in new:
            raise RefactorError("Field name: letters, digits, '_', '-' or '.' only.")
        doc = self._page(system, page)
        fields = doc.data.get("fields") or {}
        if old not in fields:
            raise RefactorError(f"No field {old!r} on {page}.")
        if new == old:
            raise RefactorError("That is already the field's name.")
        if new in fields:
            raise RefactorError(f"{page} already has a field named {new!r}.")
        doc.data["fields"] = {(new if k == old else k): v for k, v in fields.items()}
        doc.changes += 1
        for step_doc, step in self._steps(system):
            if step.get("screen") == page and isinstance(step.get("formData"), dict):
                form = {}
                for key, value in step["formData"].items():
                    name, suffix = _split(str(key))
                    if name == old:
                        key = new + suffix
                        step_doc.changes += 1
                    form[key] = value
                step["formData"] = form
            if self._rename_override(step, page, old, None, new):
                step_doc.changes += 1

    def merge_fields(self, system: str, page: str, canonical: str, duplicates: list[str]) -> None:
        """Replace fields that are exactly the same as `canonical` by it. A step that uses several of them gets
        the documented pipe repeat (name, name|, name||), in the order it already had."""
        doc = self._page(system, page)
        fields = doc.data.get("fields") or {}
        for name in [canonical, *duplicates]:
            if name not in fields:
                raise RefactorError(f"No field {name!r} on {page}.")
        if any(fields[d] != fields[canonical] for d in duplicates):
            raise RefactorError("Only fields with exactly the same settings can be merged.")
        dups = set(duplicates)
        for step_doc, step in self._steps(system):
            if step.get("screen") == page and isinstance(step.get("formData"), dict):
                produced: set[str] = set()
                form = {}
                touched = False
                for key, value in step["formData"].items():
                    name, suffix = _split(str(key))
                    if name in dups or name == canonical:
                        candidate = canonical + suffix
                        while candidate in produced:
                            if suffix.strip("|"):
                                raise RefactorError(
                                    f"{step_doc.label} uses {canonical!r} and its duplicates with repeat settings "
                                    f"in one step ({key!r}); merge them by hand.")
                            candidate += "|"
                        touched = touched or candidate != key
                        key = candidate
                    produced.add(str(key))
                    form[key] = value
                if touched:
                    step["formData"] = form
                    step_doc.changes += 1
            for d in dups:
                if self._rename_override(step, page, d, None, canonical):
                    step_doc.changes += 1
        doc.data["fields"] = {k: v for k, v in fields.items() if k not in dups}
        doc.changes += 1

    def remove_fields(self, system: str, page: str, names: list[str]) -> None:
        doc = self._page(system, page)
        fields = doc.data.get("fields") or {}
        gone = set(names)
        for step_doc, step in self._steps(system):
            used = set()
            if step.get("screen") == page and isinstance(step.get("formData"), dict):
                used = {_split(str(k))[0] for k in step["formData"]} & gone
            override = step.get("override")
            if isinstance(override, str) and override.count("|") >= 2:
                p, f, _ = override.split("|", 2)
                if p == page and f in gone:
                    used.add(f)
            if used:
                raise RefactorError(f"{', '.join(sorted(used))} still used in {step_doc.label}; "
                                    "only unused fields can be removed.")
        missing = gone - set(fields)
        if missing:
            raise RefactorError(f"No field {', '.join(sorted(missing))} on {page}.")
        doc.data["fields"] = {k: v for k, v in fields.items() if k not in gone}
        doc.changes += len(gone)

    def drop_autogen_click(self, system: str, page: str, names: list[str]) -> None:
        """autoGen "click" on a button, link, checkbox or radio does what the type already does."""
        doc = self._page(system, page)
        for name in names:
            cfg = (doc.data.get("fields") or {}).get(name)
            if isinstance(cfg, dict) and cfg.get("type") in CLICK_TYPES and \
                    str(cfg.get("autoGen", "")).strip().lower() == "click":
                del cfg["autoGen"]
                doc.changes += 1

    # ───────────── plan and apply ─────────────

    def plan(self) -> Plan:
        return Plan([d for d in self._docs.values() if d.changes], list(self._renames))


def apply_plan(plan: Plan) -> None:
    """Write the plan all-or-nothing. Raises ConflictError (nothing written) if a file changed since it was read."""
    for doc in plan.docs:
        if current_hash(doc.path) != doc.sha:
            raise ConflictError(f"{doc.label} changed on disk since the preview. Run it again.")
    for old, new in plan.renames:
        if not old.exists() or new.exists():
            raise ConflictError(f"Cannot rename {old.name} to {new.name}: the folder changed. Run it again.")
    backups = {doc.path: doc.path.read_bytes() for doc in plan.docs}
    written: list[Path] = []
    renamed: list[tuple[Path, Path]] = []
    try:
        for doc in plan.docs:
            write_json(doc.path, doc.data, doc.sha, doc.indent)
            written.append(doc.path)
        for old, new in plan.renames:
            os.replace(old, new)
            renamed.append((old, new))
    except Exception:
        for old, new in reversed(renamed):
            os.replace(new, old)
        for path in written:
            path.write_bytes(backups[path])
        raise
