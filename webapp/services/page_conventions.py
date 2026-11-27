"""How a site's pages are built, as data: the conventions the "page from HTML" builder follows.

A profile says which attribute is the site's own id, how its Yes/No radios, checkboxes, date pickers and uploads are
clicked, how its fields are named, and which autoGen rule suits which label. The built-in OPA profile is the one the
NYBE pages follow (`rn_OpaControl`, `data-attributeid`, `boolRadioY_`); it is chosen when the pasted HTML looks like
it. A workspace changes or adds to it, per system, in `Data/pageConventions.json`:

    {"NYBE": {"name_strip": "^(?:LSI|CDI)_(?:[A-Z0-9]{2,8}_)?",
              "autogen_by_label": [["\\\\bnickname\\\\b", "name"]]},
     "*": {"required_class": "mandatory"}}

"*" applies to every system; a system's entry wins over "*"; both win over the built-in profile. A list value
replaces the built-in list (so copy the entries you want to keep), a dict value is merged key by key.
No UI imports here: the profile is plain data and unit-tested.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

CONFIG_FILE = Path("Data") / "pageConventions.json"

GENERIC: dict = {
    "name": "Generic",
    "detect": [],
    "id_attribute": "",            # "" = use the attribute the live scan would find; none: locate by name / #id
    "name_strip": "",              # regex: what to cut from the front of an id value to make a field name
    "required_class": "required",
    "hidden_attr": "data-hidden",  # "1" on an element: the page captured it hidden (a branch another answer reveals)
    "yes_no": {"yes_for": "", "no_for": ""},  # label for= prefixes of the Yes and No radios ("" = by label text)
    "radio_label_class": "radio_label",
    "checkbox_label_class": "checkbox_label",
    "datepicker_class": "hasDatepicker",
    "upload": {"button_class": "", "type": "document", "autoGen": "document"},
    "split_inputs": [],            # inputs that together make one value: [{"suffixes": [...], "names": [...], ...}]
    "date_autogen": "getDateBetween(-30, 0, '%m/%d/%Y')",
    # controls that share one id container (a date in three selects): by a class of their own -> a name part and rule
    "parts": {},
    "autogen_by_label": [
        [r"\bfirst name\b|\blast name\b|\bmiddle name\b", "name"],
        [r"\bfull name\b", "fullname"],
        [r"e-?mail", "email"],
        [r"phone|telephone", "phone"],
        [r"\bzip(?: code)?\b(?!\s*\+)", "zipcode"],
        [r"address line 1|street address", "addressLine1"],
        [r"^\s*city\s*:?\s*$", "city"],
        [r"\bcounty\b", "county"],
        [r"\bein\b|employer identification|\btin\b|taxpayer id", "@id9"],
        [r"(?:business|company|firm.?s|franchisor.?s|legal|entity|employer) name\b|name of legal entity",
         "legalNameInc"],
    ],
    # button text (lower case) -> the common action a test calls
    "buttons": {"save & continue": "Save and continue", "save and continue": "Save and continue", "next": "Next",
                "proceed": "Proceed", "proceed to payment": "Proceed to payment",
                "submit application": "Submit application", "back": "Back",
                "exit application": "Exit application"},
    "default_common_action": "Save and continue",
}

OPA: dict = {
    "name": "OPA (NYBE)",
    "detect": ["rn_OpaControl", "data-opaid"],
    "id_attribute": "data-attributeid",
    "name_strip": r"^(?:LSI|CDI)_(?:[A-Z0-9]{2,8}_)?",
    "yes_no": {"yes_for": "boolRadioY_", "no_for": "boolRadioN_"},
    "upload": {"button_class": "fileUpload_button", "type": "fileChooserDocument", "autoGen": "document"},
    "split_inputs": [
        {"suffixes": ["-ssn1", "-ssn2", "-ssn3"], "names": ["Area", "Group", "Serial"], "type_keys": True,
         "autogen": ["getIntBetween(101, 666)", "getIntBetween(11, 99)", "getIntBetween(1001, 9999)"]},
    ],
    "parts": {"dmyyear": {"name": "Year", "autoGen": "getIntBetween(1960, 2006)"},
              "dmymonth": {"name": "Month", "autoGen": "getIntBetween(1, 13)"},
              "dmyday": {"name": "Day", "autoGen": "getIntBetween(1, 29)"}},
}

PROFILES = {"opa": OPA}
_KNOWN = set(GENERIC)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = copy.deepcopy(value)
    return out


def detect(html: str) -> dict:
    """The built-in profile whose markers the HTML contains (Generic when none)."""
    for profile in PROFILES.values():
        if profile["detect"] and any(marker in html for marker in profile["detect"]):
            return _merge(GENERIC, profile)
    return copy.deepcopy(GENERIC)


def load(workspace: Path | None, system: str, html: str) -> tuple[dict, list[str]]:
    """(profile, notes): the detected built-in profile with the workspace's Data/pageConventions.json applied."""
    profile, notes = detect(html), []
    if workspace is None:
        return profile, notes
    path = Path(workspace) / CONFIG_FILE
    if not path.is_file():
        return profile, notes
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return profile, [f"{CONFIG_FILE.as_posix()} could not be read ({e}); the built-in conventions are used."]
    if not isinstance(data, dict):
        return profile, [f"{CONFIG_FILE.as_posix()} must be an object of system name -> conventions."]
    for scope in ("*", system):
        entry = data.get(scope)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            notes.append(f"{CONFIG_FILE.as_posix()}: '{scope}' must be an object.")
            continue
        unknown = sorted(set(entry) - _KNOWN)
        if unknown:
            notes.append(f"{CONFIG_FILE.as_posix()}: '{scope}' has keys the builder does not know: "
                         f"{', '.join(unknown)}.")
        profile = _merge(profile, {k: v for k, v in entry.items() if k in _KNOWN})
    bad = _validate(profile)
    if bad:
        return detect(html), notes + [f"{CONFIG_FILE.as_posix()}: {bad}; the built-in conventions are used."]
    return profile, notes


def _validate(profile: dict) -> str:
    """"" when every regex in the profile compiles and the lists have the shape the builder reads."""
    patterns = [profile.get("name_strip") or ""]
    rules = profile.get("autogen_by_label")
    if not isinstance(rules, list) or any(not (isinstance(r, list) and len(r) == 2) for r in rules):
        return "autogen_by_label must be a list of [pattern, rule] pairs"
    patterns += [str(r[0]) for r in rules]
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            return f"the pattern {pattern!r} is not a valid regular expression ({e})"
    if not isinstance(profile.get("buttons"), dict) or not isinstance(profile.get("split_inputs"), list) \
            or not isinstance(profile.get("parts"), dict):
        return "buttons and parts must be objects and split_inputs a list"
    return ""
