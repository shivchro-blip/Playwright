"""Tags follow the selected system: leftovers from another system are dropped and a sensible default is chosen."""
from __future__ import annotations

from webapp.services.test_catalog import Catalog, TestCaseInfo as CaseInfo  # pytest collects Test* names


def catalog(system: str, *tag_lists: list[str]) -> Catalog:
    return Catalog(system, [CaseInfo(f"TC-{i}", tags, "f.json", 1) for i, tags in enumerate(tag_lists)])


def test_leftover_tags_from_another_system_are_dropped():
    nybe = catalog("NYBE2", ["all", "WCB", "Regression", "Smoke"], ["all", "TC_CE200_001_C_CORP"])
    assert nybe.tags_for(["GOOGLE"], "NYBE2") == ["all"]  # nothing matches: falls back to "all"
    assert nybe.tags_for(["GOOGLE", "smoke"], "NYBE2") == ["Smoke"]  # kept, spelled as the tests spell it
    assert nybe.tags_for(["WCB", "wcb", "Nope"], "NYBE2") == ["WCB"]


def test_default_is_the_systems_own_tag_then_all_then_nothing():
    assert catalog("GOOGLE", ["GOOGLE", "smoke"], ["all"]).tags_for(["WCB"], "GOOGLE") == ["GOOGLE"]
    assert catalog("SMOKE", ["E2E", "smoke"]).tags_for([], "SMOKE") == ["smoke"]  # case-insensitive system tag
    assert catalog("X", ["a"], ["b"]).tags_for(["old"], "X") == []
    assert catalog("X").tags_for(["old"], "X") == []  # a system with no tests
