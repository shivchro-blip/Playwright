"""Test data sets: keep the values a test types in a file, and run one test case over many sets.

A data file is Data/TestData/<name>.json:

    { "description": "...",
      "defaults": { "Country": "United States" },                 # in every set unless the set says otherwise
      "sets": { "albany": { "City": "Albany", "Zip": "12206" },
                "nyc":    { "City": "New York", "Zip": "10001" } } }

A test case with "dataSet": "<name>" runs once per set, as "<id>[<set>]". In its steps a value (a formData value, a
value inside an explicit action, or the value of an override) that is exactly DATA.<key>, or a part of a |-joined
value that is exactly that, is replaced by the set's value. A reused test case or common action does not see the
set, except through an override on the step that calls it.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

DATA_DIR = ("Data", "TestData")
REF = re.compile(r"^DATA\.([A-Za-z_][\w\-]*)$")
NAME_OK = re.compile(r"^[\w\-]+$")
KEY_OK = re.compile(r"^[A-Za-z_][\w\-]*$")


class DataSetError(ValueError):
    """Shown to the user as is."""


def data_dir(workspace: Path | str) -> Path:
    return Path(workspace).joinpath(*DATA_DIR)


def load_file(workspace: Path | str, name: str) -> tuple[dict, dict[str, dict]]:
    """(defaults, sets) of Data/TestData/<name>.json, validated."""
    if not NAME_OK.match(str(name or "")):
        raise DataSetError(f"Data set file name {name!r}: letters, digits, '_' or '-' only.")
    path = data_dir(workspace) / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise DataSetError(f"Data set file '{name}' not found in {path.parent}.") from None
    except (OSError, ValueError) as e:
        raise DataSetError(f"Data set file '{name}' cannot be read: {e}") from e
    return validate(data, name)


def validate(data, name: str = "data") -> tuple[dict, dict[str, dict]]:
    if not isinstance(data, dict) or not isinstance(data.get("sets"), dict) or not data["sets"]:
        raise DataSetError(f"Data set file '{name}' needs a non-empty \"sets\" object.")
    defaults = data.get("defaults")
    defaults = {} if defaults is None else defaults
    if not isinstance(defaults, dict):
        raise DataSetError(f"'defaults' in '{name}' must be an object.")
    for label, values in [("defaults", defaults), *data["sets"].items()]:
        if not isinstance(values, dict):
            raise DataSetError(f"Set '{label}' in '{name}' must be an object of values.")
        for key, value in values.items():
            if not KEY_OK.match(str(key)):
                raise DataSetError(f"Key {key!r} in '{label}' of '{name}': letters, digits, '_' or '-', "
                                   "starting with a letter or '_'.")
            if isinstance(value, (dict, list)):
                raise DataSetError(f"'{label}.{key}' in '{name}' must be text or a number.")
    for set_name in data["sets"]:
        if not NAME_OK.match(str(set_name)):
            raise DataSetError(f"Set name {set_name!r} in '{name}': letters, digits, '_' or '-' only.")
    return dict(defaults), {k: dict(v) for k, v in data["sets"].items()}


def merged(defaults: dict, values: dict) -> dict:
    return {**defaults, **values}


def _replace_string(text: str, data: dict, where: str) -> str:
    def one(part: str) -> str:
        m = REF.match(part.strip())
        if not m:
            return part
        key = m.group(1)
        if key not in data:
            have = ", ".join(sorted(data)) or "none"
            raise DataSetError(f"{where}: the data set has no value '{key}' (it has: {have}).")
        value = data[key]
        if value is None:
            return ""
        return str(value)

    if "|" in text:
        return "|".join(one(p) for p in text.split("|"))
    return one(text)


def substitute(node, data: dict, where: str):
    """A copy of `node` with every DATA.<key> value replaced. Dict keys are left alone."""
    if isinstance(node, str):
        return _replace_string(node, data, where)
    if isinstance(node, dict):
        return {k: substitute(v, data, where) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, data, where) for v in node]
    return node


def references(node) -> set[str]:
    """The data keys a value (usually a list of steps) refers to."""
    found: set[str] = set()
    if isinstance(node, str):
        for part in node.split("|"):
            m = REF.match(part.strip())
            if m:
                found.add(m.group(1))
    elif isinstance(node, dict):
        for v in node.values():
            found |= references(v)
    elif isinstance(node, list):
        for v in node:
            found |= references(v)
    return found


def expand(cases: list[dict], workspace: Path | str, only_set: str = "") -> list[dict]:
    """Test cases with a "dataSet" become one copy per set (or only `only_set`); the others pass through."""
    out: list[dict] = []
    for case in cases:
        name = case.get("dataSet")
        if not name:
            out.append(case)
            continue
        case_id = str(case.get("testCaseID", ""))
        defaults, sets = load_file(workspace, str(name))
        chosen = {only_set: sets[only_set]} if only_set and only_set in sets else sets
        if only_set and only_set not in sets:
            raise DataSetError(f"{case_id}: data set '{name}' has no set '{only_set}' "
                               f"(it has: {', '.join(sets)}).")
        for set_name, values in chosen.items():
            data = merged(defaults, values)
            copy_ = copy.deepcopy(case)
            copy_["steps"] = substitute(case.get("steps") or [], data, f"{case_id}[{set_name}]")
            copy_["testCaseID"] = f"{case_id}[{set_name}]"
            copy_["baseTestCaseID"], copy_["dataSetName"] = case_id, set_name
            out.append(copy_)
    return out
