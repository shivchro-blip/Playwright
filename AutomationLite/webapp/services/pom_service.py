"""Page objects (POM JSON) under <workspace>/Pages/<SYSTEM>/**.json."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from utils import page_components
from utils.page_components import ComponentError
from webapp.services.json_files import NAME_RE, PathError, read_json, safe_path, write_json

# Field types the engine maps to actions (utils/dynamic_form_handler.FIELD_TYPE_DEFAULT_ACTION)
FIELD_TYPES = [
    "button", "link", "checkbox", "radio", "table",
    "text", "input", "textarea", "select", "number",
    "document", "sladocument", "correctionDocument", "fileChooserDocument",
    "keyboard", "date", "datepicker",
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
    component: bool = False  # a shared component (utils/page_components.py)
    uses: tuple[str, ...] = ()  # the components the page uses


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
            component, used = False, ()
            try:
                data, _, _ = read_json(path)
                count = len(data.get("fields") or {}) if isinstance(data, dict) else 0
                component = page_components.is_component(data)
                try:
                    used = tuple(u["component"] for u in page_components.uses(data))
                except ComponentError:
                    pass
            except (OSError, ValueError):
                count = -1  # unreadable
            refs.append(PageRef(name=path.stem, rel=path.relative_to(base).as_posix(), fields=count,
                                component=component, uses=used))
        names = Counter(r.name for r in refs)
        for r in refs:
            r.duplicate = names[r.name] > 1
        return refs

    def raw(self, system: str, screen: str, index: dict[str, str] | None = None) -> dict | None:
        """The page file of a screen as it is on disk; None when missing or unreadable."""
        rel = (index if index is not None else self.screen_index(system)).get(screen)
        if not rel:
            return None
        try:
            data, _, _ = read_json(safe_path(self.pages_dir(system), rel))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def lookup(self, system: str):
        """screen name -> page file data, for utils/page_components (each file read once per lookup)."""
        cache: dict[str, dict | None] = {}
        index = self.screen_index(system)

        def find(name: str):
            if name not in cache:
                cache[name] = self.raw(system, name, index)
            return cache[name]
        return find

    def expanded_fields(self, system: str, data: dict, origins: dict | None = None) -> dict[str, dict]:
        """A page's fields with the fields of the components it uses; a page whose components cannot be resolved
        gives its own fields only (the editor and the runner report why)."""
        try:
            fields = page_components.expand_fields(data, self.lookup(system), origins)
        except ComponentError:
            fields = data.get("fields") if isinstance(data, dict) else None
            fields = fields if isinstance(fields, dict) else {}
        return {k: v for k, v in fields.items() if isinstance(v, dict)}

    def field_configs(self, system: str, screen: str) -> dict[str, dict]:
        """Field name -> config of a page as the runner sees it (its components' fields included), in order;
        {} when the page is missing or unreadable."""
        data = self.raw(system, screen)
        return self.expanded_fields(system, data) if data is not None else {}

    def field_origins(self, system: str, screen: str) -> dict[str, str]:
        """Field name -> the component it comes from, for the page's fields that are not its own."""
        data = self.raw(system, screen)
        origins: dict[str, str] = {}
        if data is not None:
            self.expanded_fields(system, data, origins)
        return origins

    def field_home(self, system: str, screen: str, field: str, lookup=None) -> tuple[str, str] | None:
        """(screen, field name) where a page's field is defined: the page itself, or the component it comes from
        (a heal or a rename of a component's field belongs in the component). None when the page has no such
        field."""
        lookup = lookup or self.lookup(system)

        def home(name: str, fname: str, depth: int):
            data = lookup(name)
            if not isinstance(data, dict) or depth > page_components.MAX_DEPTH:
                return None
            if isinstance(data.get("fields"), dict) and fname in data["fields"]:
                return name, fname
            try:
                entries = page_components.uses(data)
            except ComponentError:
                return None
            for entry in reversed(entries):  # a later component would have replaced an earlier one's field
                prefix = entry["prefix"]
                if prefix and not fname.startswith(prefix + "."):
                    continue
                found = home(entry["component"], fname[len(prefix) + 1:] if prefix else fname, depth + 1)
                if found:
                    return found
            return None
        return home(screen, field, 0)

    def aliases(self, system: str, component: str) -> dict[str, list[tuple[str, str]]]:
        """A component's field -> every (screen, field name) it is on: the component itself and each page that
        uses it (directly or through another component), under the name the page gives it. A page's own field
        of the same name replaces the component's there, so that page is not listed."""
        lookup = self.lookup(system)
        out: dict[str, list[tuple[str, str]]] = {}
        for ref in self.list_pages(system):
            data = lookup(ref.name)
            if not isinstance(data, dict):
                continue
            origins: dict[str, str] = {}
            fields = self.expanded_fields(system, data, origins)
            for full in fields:
                if ref.name != component and full not in origins:
                    continue
                found = self.field_home(system, ref.name, full, lookup)
                if found and found[0] == component:
                    out.setdefault(found[1], []).append((ref.name, full))
        return out

    def components(self, system: str) -> list[PageRef]:
        return [r for r in self.list_pages(system) if r.component]

    def users_of(self, system: str, component: str) -> list[str]:
        """The pages (and components) whose "use" names this component directly."""
        return [r.name for r in self.list_pages(system) if component in r.uses]

    def field_names(self, system: str, screen: str) -> list[str]:
        return list(self.field_configs(system, screen))

    # ───────────── read / write ─────────────

    def load(self, system: str, rel: str) -> tuple[dict, str]:
        data, sha, _ = read_json(safe_path(self.pages_dir(system), rel))
        if not isinstance(data, dict):
            raise ValueError("Page file must contain a JSON object.")
        return data, sha

    def save(self, system: str, rel: str, data: dict, expected_hash: str | None) -> str:
        self.validate(data)
        name = Path(rel).stem
        try:  # the components it uses must exist, be components and not lead back to this page
            page_components.expand_fields(data, lambda n: data if n == name else self.lookup(system)(n), None,
                                          (name,))
        except ComponentError as e:
            raise ValueError(str(e)) from None
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
        if "component" in data and not isinstance(data["component"], bool):
            raise ValueError('"component" is true (a shared component) or false.')
        try:
            page_components.uses(data)
        except ComponentError as e:
            raise ValueError(str(e)) from None

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
