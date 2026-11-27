"""Yes/No choices on a page: one control for two fields.

A question that is answered by clicking Yes or No is two fields on the page (`ChildSupport_Yes`, `ChildSupport_No`),
and only one of them may be clicked. The editor shows such a pair as one row with a Yes/No choice, and "add all page
fields" adds one of the two, instead of both. The files are not changed: the choice is written as the same
`"ChildSupport_No": "click"` entry as before. No UI imports here: this is plain data and unit-tested.
"""
from __future__ import annotations

from typing import Iterable

OPTIONS = ("Yes", "No")  # the labels of a pair; the field names end in _Yes / _No
DEFAULT_OPTION = "No"  # the one added by "all page fields" (the answer that leaves the page's other questions hidden)


def split_option(name: str) -> tuple[str, str]:
    """('ChildSupport', 'No') for 'ChildSupport_No'; (name, '') for a field that is not an option."""
    for option in OPTIONS:
        suffix = f"_{option}"
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], option
    return name, ""


def choice_groups(names: Iterable[str]) -> dict[str, dict[str, str]]:
    """base -> {option: field name} for every base that has both a _Yes and a _No field, in page order."""
    found: dict[str, dict[str, str]] = {}
    for name in names:
        base, option = split_option(name)
        if option:
            found.setdefault(base, {})[option] = name
    return {base: options for base, options in found.items() if len(options) == len(OPTIONS)}


def fields_to_add(names: Iterable[str], taken: Iterable[str] = ()) -> list[str]:
    """The fields "add all page fields" adds: every field not on the step yet, but one field per Yes/No pair
    (DEFAULT_OPTION), and none for a pair that already has one of its fields on the step."""
    names, taken = list(names), set(taken)
    groups = choice_groups(names)
    answered = {base for base, options in groups.items() if taken & set(options.values())}
    out: list[str] = []
    for name in names:
        if name in taken:
            continue
        base, option = split_option(name)
        if base in groups and option:
            if base in answered or option != DEFAULT_OPTION:
                continue
        out.append(name)
    return out
