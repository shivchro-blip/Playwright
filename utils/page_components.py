"""Shared page components: a group of fields defined once and used on many pages.

A component is a page file marked "component": true. It holds fields like any page (and may use other components),
and usually lives in Pages/<SYSTEM>/components/:

    {"component": true, "description": "The address block",
     "fields": {"street": {"locator": "input[name$='street']", "type": "text"},
                "city":   {"locator": "input[name$='city']", "type": "text"}}}

A page lists the components it uses before its own fields:

    {"path": "/apply",
     "use": ["Header",
             {"component": "Address", "prefix": "mailing", "within": "#mailing-address"},
             {"component": "Address", "prefix": "home", "within": "#home-address"}],
     "fields": {"submit": {"role": "button", "name": "Submit", "type": "button"}}}

The page then has the component's fields as if they were its own: "Header"'s fields under their names, the first
Address's as mailing.street and mailing.city, the second's as home.street and home.city. "within" looks for the
component's fields only inside that container (a CSS or XPath locator), so the same component can sit on one page
twice. A field of the page's own with the same name as a component's replaces it on that page.

Pages without "use" are read exactly as before. The runner and the app expand pages through expand() alone.
"""
from __future__ import annotations

import json
import os
from typing import Callable

Lookup = Callable[[str], "dict | None"]

MAX_DEPTH = 8


class ComponentError(ValueError):
    """A page's "use" cannot be resolved; the message says which component and why."""


def is_component(data) -> bool:
    return isinstance(data, dict) and data.get("component") is True


def uses(data) -> list[dict]:
    """The page's "use" entries, each as {"component", "prefix", "within"}; ComponentError when one is malformed."""
    if not isinstance(data, dict) or not data.get("use"):
        return []
    raw = data["use"]
    if not isinstance(raw, list):
        raise ComponentError('"use" must be a list of component names.')
    out = []
    for item in raw:
        if isinstance(item, str):
            item = {"component": item}
        if not isinstance(item, dict) or not str(item.get("component") or "").strip():
            raise ComponentError(f'Each "use" entry names a component, got {item!r}.')
        prefix = str(item.get("prefix") or "").strip()
        if "|" in prefix or prefix.endswith("."):
            raise ComponentError(f"Component prefix {prefix!r}: no '|', and no '.' at the end.")
        out.append({"component": str(item["component"]).strip(), "prefix": prefix,
                    "within": str(item.get("within") or "").strip()})
    return out


def field_name(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def scoped(cfg: dict, within: str) -> dict:
    """The field's config looked for inside `within` (inside its own "within", when it has one)."""
    if not within:
        return dict(cfg)
    inner = str(cfg.get("within") or "").strip()
    return {**cfg, "within": f"{within} >> {inner}" if inner else within}


def expand_fields(data: dict, lookup: Lookup, origins: dict | None = None, _trail: tuple = ()) -> dict:
    """The page's fields with its components' fields in: the components' first, in "use" order, then the page's own
    (which replace a component field of the same name). `origins`, when given, is filled with
    field name -> the component it came from (its outermost one, for a component inside a component)."""
    own = data.get("fields") if isinstance(data, dict) else None
    own = own if isinstance(own, dict) else {}
    entries = uses(data)
    if not entries:
        return dict(own)
    if len(_trail) >= MAX_DEPTH:
        raise ComponentError(f"Components nested too deep: {' > '.join(_trail)}.")
    out: dict = {}
    came: dict = {}
    for entry in entries:
        name = entry["component"]
        if name in _trail:
            raise ComponentError(f"Component {name!r} uses itself: {' > '.join((*_trail, name))}.")
        comp = lookup(name)
        if comp is None:
            raise ComponentError(f"Component {name!r} not found.")
        if not is_component(comp):
            raise ComponentError(f'{name!r} is a page, not a component (mark it "component": true to share it).')
        fields = expand_fields(comp, lookup, None, (*_trail, name))
        for fname, cfg in fields.items():
            if not isinstance(cfg, dict):
                continue
            full = field_name(entry["prefix"], fname)
            if full in out:
                raise ComponentError(f"Field {full!r} comes from two components ({came[full]} and {name}); "
                                     "give one of them a prefix.")
            out[full] = scoped(cfg, entry["within"])
            came[full] = name
    for fname, cfg in own.items():
        out[fname] = cfg
        came.pop(fname, None)
    if origins is not None:
        origins.update(came)
    return out


def expand(data: dict, lookup: Lookup) -> dict:
    """The page with its components' fields in "fields" (a new dict; the page is not changed). A page without
    "use" is returned as it is."""
    if not isinstance(data, dict) or not data.get("use"):
        return data
    return {**data, "fields": expand_fields(data, lookup)}


def file_lookup(paths: dict[str, str]) -> Lookup:
    """A lookup over screen name -> JSON file path (the runner's screen cache)."""
    cache: dict[str, dict | None] = {}

    def lookup(name: str):
        if name not in cache:
            path = paths.get(name)
            if not path:
                cache[name] = None
            else:
                with open(path, "r", encoding="utf-8-sig") as f:
                    cache[name] = json.load(f)
        return cache[name]
    return lookup


def folder_paths(pages_dir: str) -> dict[str, str]:
    """screen name -> path of every page file under a system's Pages folder (the last one found wins)."""
    out: dict[str, str] = {}
    for dirpath, _, filenames in os.walk(pages_dir):
        for fn in filenames:
            if fn.endswith(".json"):
                out[fn[:-5]] = os.path.join(dirpath, fn)
    return out
