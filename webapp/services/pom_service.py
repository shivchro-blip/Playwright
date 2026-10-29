"""Page objects (POM JSON) under <workspace>/Pages/<SYSTEM>/**.json."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from webapp.services.json_files import NAME_RE, PathError, read_json, safe_path, write_json

# Field types the engine maps to actions (utils/dynamic_form_handler.FIELD_TYPE_DEFAULT_ACTION)
FIELD_TYPES = [
    "button", "link", "checkbox", "radio", "table",
    "text", "input", "textarea", "select", "number",
    "document", "sladocument", "correctionDocument", "fileChooserDocument",
    "keyboard", "date",
]
# Page-level keys the engine reads (pages/dynamic_page.py)
PAGE_KEYS = ["path", "fullpath", "titleLocator", "titleContains", "errorLocators", "errorContains"]
# Common per-field keys shown as columns; anything else is kept and editable as JSON
FIELD_KEYS = ["type", "locator", "role", "name", "index", "autoGen", "contains", "placeholder", "label",
              "text", "tableId", "waitForNewTab", "focus"]

_CUSTOM_RE = re.compile(r"['\"](CUSTOMFIELD_[A-Z_]+)['\"]")


@dataclass
class PageRef:
    name: str  # screen name used in test steps (file name without .json)
    rel: str  # path relative to Pages/<SYSTEM>
    fields: int
    duplicate: bool = False  # another file has the same screen name (last one loaded wins)


class PomService:
    def __init__(self, workspace: Path, repo_root: Path):
        self.workspace = Path(workspace)
        self.repo_root = Path(repo_root)

    def pages_dir(self, system: str) -> Path:
        if not system or not NAME_RE.match(system) or system in (".", ".."):
            raise PathError(f"Invalid system: {system!r}")
        return self.workspace / "Pages" / system

    def systems(self) -> list[str]:
        root = self.workspace / "Pages"
        return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []

    # ───────────── listing ─────────────

    def screen_index(self, system: str) -> dict[str, str]:
        """screen name -> rel path, resolved in the same order as the suite's build_screen_cache."""
        base = self.pages_dir(system)
        index: dict[str, str] = {}
        for dirpath, _, filenames in os.walk(base):
            for fn in filenames:
                if fn.endswith(".json"):
                    index[fn[:-5]] = Path(dirpath, fn).relative_to(base).as_posix()
        return index

    def list_pages(self, system: str) -> list[PageRef]:
        base = self.pages_dir(system)
        if not base.is_dir():
            return []
        refs = []
        for path in sorted(base.rglob("*.json"), key=lambda p: p.relative_to(base).as_posix().casefold()):
            try:
                data, _, _ = read_json(path)
                count = len(data.get("fields") or {}) if isinstance(data, dict) else 0
            except (OSError, ValueError):
                count = -1  # unreadable
            refs.append(PageRef(name=path.stem, rel=path.relative_to(base).as_posix(), fields=count))
        names = Counter(r.name for r in refs)
        for r in refs:
            r.duplicate = names[r.name] > 1
        return refs

    def field_names(self, system: str, screen: str) -> list[str]:
        rel = self.screen_index(system).get(screen)
        if not rel:
            return []
        try:
            data, _, _ = read_json(safe_path(self.pages_dir(system), rel))
        except (OSError, ValueError):
            return []
        return list((data.get("fields") or {}).keys()) if isinstance(data, dict) else []

    # ───────────── read / write ─────────────

    def load(self, system: str, rel: str) -> tuple[dict, str]:
        data, sha, _ = read_json(safe_path(self.pages_dir(system), rel))
        if not isinstance(data, dict):
            raise ValueError("Page file must contain a JSON object.")
        return data, sha

    def save(self, system: str, rel: str, data: dict, expected_hash: str | None) -> str:
        self.validate(data)
        return write_json(safe_path(self.pages_dir(system), rel), data, expected_hash)

    def create(self, system: str, folder: str, name: str) -> str:
        name = name.strip().removesuffix(".json")
        if not name or not re.match(r"^[A-Za-z0-9_\-]+$", name):
            raise PathError("Page name: letters, digits, '_' or '-' only.")
        if name in self.screen_index(system):
            raise PathError(f"A page named {name!r} already exists in {system}.")
        folder = folder.strip().strip("/\\")
        rel = f"{folder}/{name}.json" if folder else f"{name}.json"
        write_json(safe_path(self.pages_dir(system), rel), {"fields": {}}, None, indent="  ")
        return rel

    @staticmethod
    def validate(data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("Page must be a JSON object.")
        fields = data.get("fields")
        if not isinstance(fields, dict):
            raise ValueError("Page needs a 'fields' object.")
        bad = [k for k, v in fields.items() if not isinstance(v, dict)]
        if bad:
            raise ValueError(f"Field config must be an object: {', '.join(bad)}")
        if data.get("path") and data.get("fullpath"):
            raise ValueError("Use either 'path' or 'fullpath', not both.")

    # ───────────── reference lists ─────────────

    def autogen_rules(self) -> list[str]:
        path = self.workspace / "Data" / "rand_gen_rules.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return []
        return sorted(data.get("rand_gen_rules", {}), key=str.casefold)

    def custom_fields(self) -> list[str]:
        """CUSTOMFIELD_* step keys the engine understands (from utils/custom_field_helper.py)."""
        try:
            src = (self.repo_root / "utils" / "custom_field_helper.py").read_text(encoding="utf-8-sig")
        except OSError:
            return []
        return sorted(set(_CUSTOM_RE.findall(src)))
