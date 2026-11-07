"""The command palette: what it offers and how a query ranks it."""
from types import SimpleNamespace

from webapp.services.commands import Command, build, rank, score
from webapp.services.workspace_model import TestEntry as Entry


def snapshot():
    return SimpleNamespace(
        tests={("SMOKE", "TC-SMOKE-001-Search"): Entry("TC-SMOKE-001-Search", "SMOKE", "SMOKE",
                                                            "SmokeTests.json", 0, ["smoke"], []),
               ("PORTAL", "TC-LOGIN-001"): Entry("TC-LOGIN-001", "PORTAL", "PORTAL", "Login/Login.json", 2,
                                                      ["login", "smoke"], [])},
        pages={("SMOKE", "SmokeHome"): {"searchBox", "notes"}, ("PORTAL", "LoginPage"): {"username", "password"}},
        journeys=[{"id": "j1", "name": "Renew a licence", "system": "PORTAL"}])


def runs():
    def run(rid, status):
        return SimpleNamespace(id=rid, status=status, created_at="2026-11-07T10:00:00",
                               config=SimpleNamespace(system="SMOKE", env="dev", tags=["smoke"]))
    return [run("20261107-100000-aaaa", "passed"), run("20261107-090000-bbbb", "failed")]


def titles(query, commands=None):
    return [c.title for c in rank(query, commands or build(snapshot(), runs()))]


def test_everything_is_offered_with_what_to_do():
    by_id = {c.id: c for c in build(snapshot(), runs())}
    assert by_id["tab:History"].action == "tab"
    assert by_id["test:PORTAL:TC-LOGIN-001"].args == {"system": "PORTAL", "rel": "Login/Login.json", "index": 2}
    assert by_id["page:SMOKE:SmokeHome"].action == "open_page"
    assert by_id["field:PORTAL:LoginPage:username"].args == {"system": "PORTAL", "page": "LoginPage",
                                                             "field": "username"}
    assert by_id["journey:PORTAL:j1"].args == {"system": "PORTAL", "id": "j1"}
    assert by_id["run:20261107-090000-bbbb"].subtitle.startswith("Run · SMOKE / dev · failed")


def test_empty_query_lists_sections_and_latest_runs():
    found = rank("", build(snapshot(), runs()))
    assert [c.kind for c in found][:10] == ["tab"] * 10 and found[10].kind == "run"


def test_ranking_prefers_exact_then_start_then_word_start():
    assert titles("history")[0] == "History"
    assert titles("login")[:2] == ["LoginPage", "TC-LOGIN-001"]
    assert titles("search")[0] == "searchBox"  # starts with it, before TC-SMOKE-001-Search (later word)
    assert "TC-SMOKE-001-Search" in titles("search")


def test_letters_in_order_and_every_word_must_match():
    assert "TC-SMOKE-001-Search" in titles("tcsm")
    assert titles("renew licence") == ["Renew a licence"]
    assert titles("renew nothing") == []
    assert "TC-LOGIN-001" in titles("login portal")  # 'portal' matches the details line


def test_keywords_find_a_section_by_what_it_is_for():
    assert titles("cron")[0] == "Schedules"
    assert titles("failed")[0] == "20261107-090000-bbbb"


def test_score_is_zero_for_no_match_and_shorter_titles_win_ties():
    assert score("zzz", Command("x", "tab", "History")) == 0
    long = Command("a", "page", "SearchResultsPageWithManyWords")
    short = Command("b", "page", "SearchBox")
    assert score("search", short) > score("search", long)
