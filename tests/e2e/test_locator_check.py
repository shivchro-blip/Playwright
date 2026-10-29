"""Live locator check against the local smoke page (headless Chromium)."""
from __future__ import annotations

from webapp.services.locator_check import check_locator


def test_single_match_has_a_screenshot(site_url):
    result = check_locator(f"{site_url}/index.html", {"locator": "#q"})
    assert result.ok and result.error == "" and result.count == 1, result
    assert result.matches[0]["tag"] == "input" and result.matches[0]["visible"]
    assert result.screenshot and result.screenshot.startswith(b"\x89PNG")
    assert result.title == "AutomateNY smoke page" and result.warnings == []


def test_many_matches_warn_about_strict_mode_and_index_picks_one(site_url):
    many = check_locator(f"{site_url}/index.html", {"locator": "input"})
    assert many.count == 2 and any("strict mode" in w for w in many.warnings)
    picked = check_locator(f"{site_url}/index.html", {"locator": "input", "index": 1})
    assert picked.description.endswith(".nth(1)") and picked.warnings == []
    assert [m["selected"] for m in picked.matches] == [False, True]
    out_of_range = check_locator(f"{site_url}/index.html", {"locator": "input", "index": 5})
    assert any("out of range" in w for w in out_of_range.warnings)


def test_label_and_missing_and_invalid_locators(site_url):
    assert check_locator(f"{site_url}/index.html", {"label": "Notes"}).count == 1
    missing = check_locator(f"{site_url}/index.html", {"locator": "#nope"})
    assert missing.ok and missing.count == 0
    invalid = check_locator(f"{site_url}/index.html", {"locator": "##bad["})
    assert not invalid.ok and invalid.error
    tokens = check_locator(f"{site_url}/index.html", {"locator": "#DICT.user"})
    assert any("DICT./ENV." in w for w in tokens.warnings)
