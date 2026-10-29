"""Test case files under <workspace>/Tests/<SYSTEM>/MasterRegressionSuite/**.json.

A test is addressed by (file, index) because testCaseIDs are not guaranteed unique.
Saving rewrites only that entry; other test cases and unknown keys in the file are kept.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from webapp.services.json_files import ConflictError, NAME_RE, PathError, read_json, safe_path, write_json
from webapp.services.pom_service import PomService

SUITE_DIR = "MasterRegressionSuite"
STEP_KINDS = ("screen", "commonAction", "testCase", "API_REQUEST")


@dataclass
class TestRef:
    file: str
    index: int
    test_id: str
    tags: list[str]
    steps: int
    duplicate: bool = False


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)  # block saving
    warnings: list[str] = field(default_factory=list)  # shown, saving allowed


def new_test_case(system: str, test_id: str) -> dict:
    return {"testCaseID": test_id, "system": system, "env": "dev", "module": "", "tags": [system],
            "description": "", "steps": []}


def base_field(key: str) -> str:
    """'SSN|' / 'Field|ELoop(3)' -> 'SSN' / 'Field' (pipe suffixes allow repeated keys)."""
    return key.split("|", 1)[0]


class TestCaseService:
    def __init__(self, workspace: Path, pom: PomService):
        self.workspace = Path(workspace)
        self.pom = pom

    def suite_dir(self, system: str) -> Path:
        if not system or not NAME_RE.match(system) or system in (".", "..", "com"):
            raise PathError(f"Invalid system: {system!r}")
        return self.workspace / "Tests" / system / SUITE_DIR

    def systems(self) -> list[str]:
        root = self.workspace / "Tests"
        return sorted(p.name for p in root.iterdir() if (p / SUITE_DIR).is_dir()) if root.is_dir() else []

    # ───────────── listing ─────────────

    def files(self, system: str) -> list[str]:
        base = self.suite_dir(system)
        if not base.is_dir():
            return []
        return sorted((p.relative_to(base).as_posix() for p in base.rglob("*.json")), key=str.casefold)

    def list_tests(self, system: str) -> tuple[list[TestRef], list[str]]:
        refs, errors = [], []
        for rel in self.files(system):
            try:
                data, _ = self.load_file(system, rel)
            except (OSError, ValueError) as e:
                errors.append(f"{rel}: {e}")
                continue
            for i, tc in enumerate(data.get("testCases", [])):
                if isinstance(tc, dict):
                    refs.append(TestRef(rel, i, str(tc.get("testCaseID", "")), list(tc.get("tags") or []),
                                        len(tc.get("steps") or [])))
        counts = Counter(r.test_id for r in refs)
        for r in refs:
            r.duplicate = counts[r.test_id] > 1
        return refs, errors

    def common_actions(self) -> dict[str, str]:
        """commonAction name -> file (relative to Tests/com). Later files win, like the suite."""
        base = self.workspace / "Tests" / "com"
        out: dict[str, str] = {}
        for path in sorted(base.rglob("*.json")) if base.is_dir() else []:
            try:
                data, _, _ = read_json(path)
            except (OSError, ValueError):
                continue
            for name in (data.get("commonActions") or {}) if isinstance(data, dict) else {}:
                out[name] = path.relative_to(base).as_posix()
        return dict(sorted(out.items(), key=lambda kv: kv[0].casefold()))

    def all_test_ids(self) -> list[str]:
        ids = set()
        for system in self.systems():
            refs, _ = self.list_tests(system)
            ids.update(r.test_id for r in refs if r.test_id)
        return sorted(ids, key=str.casefold)

    # ───────────── read / write ─────────────

    def load_file(self, system: str, rel: str) -> tuple[dict, str]:
        data, sha, _ = read_json(safe_path(self.suite_dir(system), rel))
        if not isinstance(data, dict) or not isinstance(data.get("testCases", []), list):
            raise ValueError("Test file must be an object with a 'testCases' list.")
        data.setdefault("testCases", [])
        return data, sha

    def get(self, system: str, rel: str, index: int) -> tuple[dict, str]:
        data, sha = self.load_file(system, rel)
        try:
            return data["testCases"][index], sha
        except IndexError as e:
            raise ConflictError("That test case no longer exists in the file. Reload the list.") from e

    def save(self, system: str, rel: str, index: int | None, test_case: dict,
             expected_hash: str | None) -> tuple[int, str]:
        """Replace testCases[index] (or append when index is None). Returns (index, new hash)."""
        result = self.validate(system, test_case)
        if result.errors:
            raise ValueError("; ".join(result.errors))
        path = safe_path(self.suite_dir(system), rel)
        if path.exists():
            data, sha = self.load_file(system, rel)
            if expected_hash is not None and sha != expected_hash:
                raise ConflictError(f"{rel} was changed on disk since you opened it. Reload it first.")
            expected_hash = sha
        else:
            data, expected_hash = {"testCases": []}, None
        cases = data["testCases"]
        if index is None:
            cases.append(test_case)
            index = len(cases) - 1
        elif 0 <= index < len(cases):
            cases[index] = test_case
        else:
            raise ConflictError("That test case no longer exists in the file. Reload the list.")
        return index, write_json(path, data, expected_hash)

    def delete(self, system: str, rel: str, index: int, expected_hash: str) -> str:
        data, sha = self.load_file(system, rel)
        if sha != expected_hash:
            raise ConflictError(f"{rel} was changed on disk since you opened it. Reload it first.")
        if not 0 <= index < len(data["testCases"]):
            raise ConflictError("That test case no longer exists in the file.")
        del data["testCases"][index]
        return write_json(safe_path(self.suite_dir(system), rel), data, sha)

    @staticmethod
    def normalize_file_name(name: str) -> str:
        name = name.strip().replace("\\", "/").strip("/")
        if not name.lower().endswith(".json"):
            name += ".json"
        if not re.match(r"^[\w\-/ ]+\.json$", name):
            raise PathError("File name: letters, digits, spaces, '_', '-' or '/' only.")
        return name

    # ───────────── validation ─────────────

    def validate(self, system: str, tc: dict) -> Validation:
        v = Validation()
        if not isinstance(tc, dict):
            v.errors.append("Test case must be a JSON object.")
            return v
        test_id = str(tc.get("testCaseID") or "").strip()
        if not test_id:
            v.errors.append("testCaseID is required.")
        steps = tc.get("steps")
        if not isinstance(steps, list):
            v.errors.append("'steps' must be a list.")
            steps = []
        if not tc.get("tags"):
            v.warnings.append("No tags - the Control tab can only run tagged tests.")
        if not steps:
            v.warnings.append("No steps - the framework skips tests without steps.")

        step_system = tc.get("system") or system
        try:
            screens = self.pom.screen_index(step_system)
        except PathError:
            screens = {}
        common = self.common_actions()
        custom = set(self.pom.custom_fields())
        known_ids: set[str] | None = None
        for n, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                v.errors.append(f"Step {n}: must be an object.")
                continue
            kinds = [k for k in STEP_KINDS if k in step]
            if len(kinds) != 1:
                v.errors.append(f"Step {n}: needs exactly one of {', '.join(STEP_KINDS)}.")
                continue
            kind, target = kinds[0], step[kinds[0]]
            if kind == "screen":
                if target not in screens:
                    v.warnings.append(f"Step {n}: page '{target}' not found in Pages/{step_system}.")
                    continue
                fields = set(self.pom.field_names(step_system, target))
                for key in (step.get("formData") or {}):
                    name = base_field(key)
                    if name not in fields and name not in custom and not name.startswith("CUSTOMFIELD_"):
                        v.warnings.append(f"Step {n}: field '{name}' is not on page '{target}'.")
            elif kind == "commonAction" and target not in common:
                v.warnings.append(f"Step {n}: common action '{target}' not found in Tests/com.")
            elif kind == "testCase":
                known_ids = known_ids if known_ids is not None else set(self.all_test_ids())
                if target not in known_ids:
                    v.warnings.append(f"Step {n}: reusable test case '{target}' not found.")
        return v
