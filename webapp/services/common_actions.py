"""Common actions (Tests/com/*.json): steps shared by many test cases, called with {"commonAction": "<name>"}.

A file holds {"commonActions": {"<name>": {"steps": [{"screen": ..., "formData": {...}}, ...]}}}. A test case can
change one value of a call with  "override": "<screen>|<field>|<value>"  and repeat it with  "loop": N, so the
fields of the action's steps are its parameters. This service lists the actions, saves one into its file (the file's
hash protects against overwriting somebody else's edit, and the edit history keeps the old version), checks it,
renames it everywhere it is called, and says who calls it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from webapp.services.json_files import ConflictError, PathError, read_json, safe_path, write_json
from webapp.services.test_case_service import TestCaseService, base_field
from webapp.services.usage import iter_steps, list_sources

NAME_OK = re.compile(r"^[\w .\-]{1,80}$")
DEFAULT_FILE = "commonActions.json"


@dataclass
class ActionRef:
    name: str
    file: str  # relative to Tests/com
    steps: int
    used_by: int


@dataclass
class Parameter:
    screen: str
    field: str
    value: str

    @property
    def override(self) -> str:
        return f"{self.screen}|{self.field}|<value>"


@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CommonActionService:
    def __init__(self, workspace: Path, tests: TestCaseService):
        self.workspace = Path(workspace)
        self.tests = tests

    @property
    def folder(self) -> Path:
        return self.workspace / "Tests" / "com"

    # ───────────── read ─────────────

    def _files(self) -> list[Path]:
        return sorted(self.folder.rglob("*.json")) if self.folder.is_dir() else []

    def locate(self, name: str) -> tuple[Path, dict, str] | None:
        """(file, whole file data, its hash) of the action; the last file wins, like the suite."""
        found = None
        for path in self._files():
            try:
                data, sha, _ = read_json(path)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and name in (data.get("commonActions") or {}):
                found = (path, data, sha)
        return found

    def list(self) -> list[ActionRef]:
        uses = self.used_by_all()
        out: dict[str, ActionRef] = {}
        for path in self._files():
            try:
                data, _, _ = read_json(path)
            except (OSError, ValueError):
                continue
            actions = data.get("commonActions") if isinstance(data, dict) else None
            for name, action in (actions or {}).items():
                steps = action.get("steps") if isinstance(action, dict) else None
                out[name] = ActionRef(name, path.relative_to(self.folder).as_posix(),
                                      len(steps) if isinstance(steps, list) else 0, len(uses.get(name, [])))
        return sorted(out.values(), key=lambda a: a.name.casefold())

    def load(self, name: str) -> tuple[dict, str, str]:
        """(the action, its file relative to Tests/com, the file's hash)."""
        found = self.locate(name)
        if found is None:
            raise PathError(f"No common action named {name!r}.")
        path, data, sha = found
        return data["commonActions"][name], path.relative_to(self.folder).as_posix(), sha

    def path_of(self, name: str) -> Path:
        found = self.locate(name)
        if found is None:
            raise PathError(f"No common action named {name!r}.")
        return found[0]

    def used_by_all(self) -> dict[str, list[str]]:
        """action name -> "<test ID> step N" for every test step that calls it."""
        out: dict[str, list[str]] = {}
        for source in list_sources(self.tests):
            if source.kind != "test":
                continue
            try:
                data, _, _ = read_json(source.path)
            except (OSError, ValueError):
                continue
            for _, owner, n, _, step in iter_steps(data, source):
                if isinstance(step.get("commonAction"), str):
                    out.setdefault(step["commonAction"], []).append(f"{owner} step {n}")
        return out

    def used_by(self, name: str) -> list[str]:
        return self.used_by_all().get(name, [])

    @staticmethod
    def parameters(action: dict) -> list[Parameter]:
        """The values a test can change with an override: every field of every step's formData."""
        out = []
        for step in (action or {}).get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("formData"), dict):
                for key, value in step["formData"].items():
                    out.append(Parameter(str(step.get("screen", "")), base_field(str(key)),
                                         value if isinstance(value, str) else repr(value)))
        return out

    # ───────────── check ─────────────

    def check(self, action) -> Check:
        result = Check()
        if not isinstance(action, dict) or not isinstance(action.get("steps"), list):
            result.errors.append('A common action is an object with a "steps" list.')
            return result
        pages = self._pages()
        for n, step in enumerate(action["steps"], 1):
            if not isinstance(step, dict):
                result.errors.append(f"Step {n}: must be an object.")
                continue
            screen = step.get("screen")
            if not isinstance(screen, str) or not screen.strip():
                result.errors.append(f'Step {n}: needs a "screen" (the page name).')
                continue
            form = step.get("formData", {})
            if not isinstance(form, dict):
                result.errors.append(f'Step {n}: "formData" must be an object.')
                continue
            if screen not in pages:
                result.warnings.append(f"Step {n}: no page named '{screen}' in any system.")
                continue
            missing = sorted({base_field(str(k)) for k in form} - pages[screen])
            if missing:
                result.warnings.append(f"Step {n}: '{screen}' has no field {', '.join(missing)}.")
        return result

    def _pages(self) -> dict[str, set[str]]:
        """page name -> field names, across every system."""
        out: dict[str, set[str]] = {}
        for system in self.tests.systems() or []:
            for ref in self.tests.pom.list_pages(system):
                try:
                    data, _ = self.tests.pom.load(system, ref.rel)
                except (OSError, ValueError):
                    continue
                out.setdefault(ref.rel.rsplit("/", 1)[-1].removesuffix(".json"), set()).update(
                    (data.get("fields") or {}).keys())
        return out

    # ───────────── write ─────────────

    def save(self, name: str, action: dict, expected_hash: str) -> str:
        """Replace the action in its file; returns the file's new hash."""
        check = self.check(action)
        if check.errors:
            raise ValueError("; ".join(check.errors))
        path, data, _ = self.locate(name) or (None, None, None)
        if path is None:
            raise PathError(f"No common action named {name!r}.")
        data["commonActions"][name] = action
        return write_json(path, data, expected_hash)

    def create(self, name: str, file: str = DEFAULT_FILE) -> str:
        name = name.strip()
        if not NAME_OK.match(name):
            raise PathError("Name: letters, digits, spaces, '_', '-' or '.' (at most 80 characters).")
        if self.locate(name):
            raise PathError(f"A common action named {name!r} already exists.")
        path = safe_path(self.folder, file or DEFAULT_FILE)
        try:
            data, sha, _ = read_json(path) if path.exists() else ({"commonActions": {}}, None, "")
        except (OSError, ValueError) as e:
            raise PathError(f"{path.name} cannot be read: {e}") from e
        data.setdefault("commonActions", {})[name] = {"steps": []}
        write_json(path, data, sha, indent="  " if sha is None else None)
        return name

    def delete(self, name: str) -> None:
        users = sorted({u.rsplit(" step ", 1)[0] for u in self.used_by(name)})
        if users:
            raise ConflictError(f"{name} is called by {', '.join(users[:5])}{' and more' if len(users) > 5 else ''}; "
                                "remove those steps first.")
        path, data, sha = self.locate(name) or (None, None, None)
        if path is None:
            raise PathError(f"No common action named {name!r}.")
        del data["commonActions"][name]
        write_json(path, data, sha)

    def rename_preview(self, old: str, new: str) -> list[str]:
        """The test files whose steps will change, as display paths."""
        self._check_rename(old, new)
        return sorted({s.label for s in list_sources(self.tests) if s.kind == "test"
                       and self._calls(s.path, old)})

    def rename(self, old: str, new: str) -> int:
        """Rename the action and every {"commonAction": old} step. Returns how many steps changed."""
        self._check_rename(old, new)
        changed = 0
        for source in list_sources(self.tests):
            if source.kind != "test":
                continue
            try:
                data, sha, _ = read_json(source.path)
            except (OSError, ValueError):
                continue
            touched = 0
            for case in (data.get("testCases") or []) if isinstance(data, dict) else []:
                for step in (case.get("steps") or []) if isinstance(case, dict) else []:
                    if isinstance(step, dict) and step.get("commonAction") == old:
                        step["commonAction"] = new
                        touched += 1
            if touched:
                write_json(source.path, data, sha)  # the history keeps the file as it was
                changed += touched
        path, data, sha = self.locate(old) or (None, None, None)
        data["commonActions"] = {(new if k == old else k): v for k, v in data["commonActions"].items()}
        write_json(path, data, sha)
        return changed

    def _check_rename(self, old: str, new: str) -> None:
        if not NAME_OK.match((new or "").strip()):
            raise PathError("Name: letters, digits, spaces, '_', '-' or '.' (at most 80 characters).")
        if self.locate(old) is None:
            raise PathError(f"No common action named {old!r}.")
        if new.strip() != old and self.locate(new.strip()):
            raise PathError(f"A common action named {new.strip()!r} already exists.")

    def _calls(self, path: Path, name: str) -> bool:
        try:
            data, _, _ = read_json(path)
        except (OSError, ValueError):
            return False
        return any(isinstance(step, dict) and step.get("commonAction") == name
                   for case in (data.get("testCases") or []) if isinstance(case, dict)
                   for step in (case.get("steps") or []))
