"""What the test and page lists show, without the clutter: folders to filter by, tags that say something.

Plain functions, no UI imports, unit-tested. Used by the Control tab's test table, the Generator's test and page
lists and the Control tag picker.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

NO_FOLDER = "."  # the filter value for files that sit directly in the suite or pages folder
ALL_FOLDERS = "*"  # the filter value for "every folder" (not empty: a select treats "" as no choice)


def folder_of(rel: str) -> str:
    """'CE200/TC_1.json' -> 'CE200'; 'a/b/c.json' -> 'a/b'; a file in the top folder -> ''."""
    return rel.replace("\\", "/").rpartition("/")[0]


def folder_choices(folders: Iterable[str]) -> dict[str, str]:
    """The folder filter's options: value -> label with the count, All first; {} when there is nothing to filter
    (fewer than two folders)."""
    counts = Counter(f or NO_FOLDER for f in folders)
    if len(counts) < 2:
        return {}
    total = sum(counts.values())
    labels = {ALL_FOLDERS: f"All folders ({total})"}
    for folder in sorted(counts, key=lambda f: ("" if f == NO_FOLDER else f).casefold()):
        labels[folder] = f"{'(top level)' if folder == NO_FOLDER else folder} ({counts[folder]})"
    return labels


def in_folder(rel_folder: str, wanted: str) -> bool:
    """Does an item whose folder is rel_folder belong to the filter value `wanted`?"""
    if wanted == ALL_FOLDERS:
        return True
    return (rel_folder or NO_FOLDER) == wanted


def visible_tags(test_id: str, tags: Iterable[str]) -> list[str]:
    """The tags worth showing: a tag equal to the test's own ID says nothing the ID does not already say."""
    own = (test_id or "").strip().casefold()
    return [t for t in tags if t.strip().casefold() != own]


def picker_tags(all_tags: Iterable[str], tests: Iterable[tuple[str, Iterable[str]]]) -> list[str]:
    """The tags to offer in the tag picker: all of them except one that is only the ID of the single test that
    carries it (selecting a test is done in the table). `tests` is (test ID, tags) per test."""
    tests = [(tid, list(tags)) for tid, tags in tests]
    ids = {tid.strip().casefold() for tid, _ in tests}
    uses = Counter(t.strip().casefold() for _, tags in tests for t in tags)
    return [t for t in all_tags if not (t.strip().casefold() in ids and uses[t.strip().casefold()] <= 1)]
