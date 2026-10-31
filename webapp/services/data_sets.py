"""Test data files under <workspace>/Data/TestData (see utils/data_sets.py): list, read, save, create, delete."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from utils.data_sets import NAME_OK, data_dir, validate
from webapp.services.json_files import (
    ConflictError, PathError, current_hash, read_json, safe_path, write_json,
)
from webapp.services.usage import list_sources


@dataclass
class DataFileRef:
    name: str
    sets: int  # -1: unreadable
    keys: int
    used_by: int = 0


class DataSetService:
    def __init__(self, workspace: Path, tests):
        self.workspace = Path(workspace)
        self.tests = tests  # TestCaseService, for who uses a file

    @property
    def folder(self) -> Path:
        return data_dir(self.workspace)

    def _path(self, name: str) -> Path:
        if not NAME_OK.match(name or ""):
            raise PathError("Name: letters, digits, '_' or '-' only.")
        return safe_path(self.folder, f"{name}.json")

    def names(self) -> list[str]:
        return sorted((p.stem for p in self.folder.glob("*.json")), key=str.casefold) if self.folder.is_dir() else []

    def list(self) -> list[DataFileRef]:
        uses = self.used_by_all()
        refs = []
        for name in self.names():
            try:
                defaults, sets = validate(read_json(self._path(name))[0], name)
                keys = set(defaults).union(*sets.values())
                refs.append(DataFileRef(name, len(sets), len(keys), len(uses.get(name, []))))
            except (OSError, ValueError):
                refs.append(DataFileRef(name, -1, 0, len(uses.get(name, []))))
        return refs

    def load(self, name: str) -> tuple[dict, str]:
        data, sha, _ = read_json(self._path(name))
        if not isinstance(data, dict):
            raise ValueError("A data file must contain a JSON object.")
        return data, sha

    def keys(self, name: str) -> list[str]:
        """Every key any set or the defaults define, in the order they first appear."""
        try:
            defaults, sets = validate(self.load(name)[0], name)
        except (OSError, ValueError):
            return []
        seen: dict[str, None] = dict.fromkeys(defaults)
        for values in sets.values():
            seen.update(dict.fromkeys(values))
        return list(seen)

    def save(self, name: str, data: dict, expected_hash: str | None) -> str:
        validate(data, name)  # raises DataSetError (a ValueError)
        return write_json(self._path(name), data, expected_hash)

    def create(self, name: str) -> str:
        name = name.strip().removesuffix(".json")
        if name in self.names():
            raise PathError(f"A data file named {name!r} already exists.")
        write_json(self._path(name), {"description": "", "defaults": {}, "sets": {"default": {}}}, None,
                   indent="  ")
        return name

    def delete(self, name: str, expected_hash: str) -> None:
        users = self.used_by_all().get(name, [])
        if users:
            names = sorted(set(users))
            raise ConflictError(f"{name} is used by {', '.join(names[:5])}{' and more' if len(names) > 5 else ''}; "
                                "remove it from those test cases first.")
        path = self._path(name)
        read_json(path)  # must exist
        if current_hash(path) != expected_hash:
            raise ConflictError(f"{name}.json was changed on disk since you opened it. Reload it first.")
        path.unlink()

    # ───────────── who uses a file ─────────────

    def used_by_all(self) -> dict[str, list[str]]:
        """data file name -> test case IDs that name it."""
        out: dict[str, list[str]] = {}
        for source in list_sources(self.tests):
            if source.kind != "test":
                continue
            try:
                data, _, _ = read_json(source.path)
            except (OSError, ValueError):
                continue
            for case in (data.get("testCases") or []) if isinstance(data, dict) else []:
                if isinstance(case, dict) and case.get("dataSet"):
                    out.setdefault(str(case["dataSet"]), []).append(str(case.get("testCaseID", "")))
        return out

    def used_by(self, name: str) -> list[str]:
        return sorted(set(self.used_by_all().get(name, [])), key=str.casefold)

