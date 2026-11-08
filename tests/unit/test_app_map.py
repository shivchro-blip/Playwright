"""Application map: screens, the moves between them, and each screen's coverage state."""
from __future__ import annotations

from webapp.services.app_map import build, mermaid, sequence

COMMON = {"login": [{"screen": "Login"}, {"screen": "Home"}]}
TESTS = {
    "T1": [{"commonAction": "login"}, {"screen": "Apply"}, {"screen": "Pay"}],
    "T2": [{"commonAction": "login"}, {"screen": "Apply"}, {"screen": "Apply"}, {"screen": "Done"}],
    "T3": [{"testCase": "T1"}, {"screen": "Receipt"}],
    "T4": [],
}
PAGES = {n: f"{n}.json" for n in ("Login", "Home", "Apply", "Pay", "Done", "Receipt", "Admin", "NewOne", "Header")}


def test_sequence_follows_common_actions_and_reused_tests():
    assert sequence(TESTS["T3"], COMMON, TESTS) == ["Login", "Home", "Apply", "Pay", "Receipt"]
    loop = {"A": [{"testCase": "A"}, {"screen": "X"}]}
    assert sequence(loop["A"], {}, loop).count("X") == 4  # nesting stops at the depth limit


def test_screens_moves_and_states():
    m = build("S", PAGES, TESTS, COMMON,
              [{"name": "Payment", "system": "S", "criticality": "critical", "tests": ["T1"]},
               {"name": "Other", "system": "X", "tests": ["T2"]}],
              {"uat": {"T1": "failed", "T2": "passed", "T3": "passed"}, "dev": {"T2": "passed"}},
              components={"Header"}, page_age_days={"NewOne": 2, "Done": 3, "Apply": 100})
    by = {s.name: s for s in m.screens}
    assert "Header" not in by  # a shared component is part of pages, not a screen
    assert by["Admin"].state == "untested" and by["Admin"].tests == []
    assert by["NewOne"].state == "untested" and by["NewOne"].new  # new and with no test: untested shows first
    assert by["Done"].state == "new" and by["Done"].tests == ["T2"]
    assert by["Pay"].state == "failing" and by["Pay"].results == {"uat": {"passed": 1, "failed": 1}}
    assert by["Pay"].critical and by["Pay"].journeys == ["Payment"]
    assert by["Apply"].journeys == ["Payment"]  # "Other" belongs to another system
    assert by["Login"].entry == 3 and by["Receipt"].tests == ["T3"]
    assert by["Receipt"].state == "tested" and by["Receipt"].results == {"uat": {"passed": 1, "failed": 0}}
    moves = {(mv.source, mv.target): mv.tests for mv in m.moves}
    assert moves[("Login", "Home")] == 3 and moves[("Home", "Apply")] == 3 and moves[("Apply", "Pay")] == 2
    assert ("Apply", "Apply") not in moves  # staying on a screen is not a move
    assert m.summary() == {"screens": 8, "moves": 5, "untested": 2, "failing": 4, "new": 1, "tested": 1}
    assert m.envs == ["dev", "uat"] and [s.state for s in m.screens][:4] == ["failing"] * 4  # where unknown


def test_a_failure_counts_against_the_screen_it_failed_on():
    m = build("S", PAGES, TESTS, COMMON, [], {"uat": {"T1": ("failed", "Pay"), "T2": "passed",
                                                      "T3": ("failed", "Nowhere")}})
    by = {s.name: s for s in m.screens}
    assert by["Pay"].state == "failing" and by["Pay"].results["uat"] == {"passed": 0, "failed": 2}  # T3 unknown
    assert by["Apply"].results["uat"] == {"passed": 2, "failed": 1}  # T1 got through it; T3's place is unknown
    assert by["Receipt"].state == "failing"  # T3 failed somewhere unknown: counts against all its screens
    m2 = build("S", PAGES, TESTS, COMMON, [], {"uat": {"T3": ("failed", "Apply")}})
    by2 = {s.name: s for s in m2.screens}
    assert by2["Apply"].state == "failing" and by2["Home"].results["uat"] == {"passed": 1, "failed": 0}
    assert "uat" not in by2["Receipt"].results  # never reached: no result
    assert by2["Home"].state == "tested"


def test_mermaid_colors_by_state_and_keeps_attention_screens_when_trimming():
    m = build("S", PAGES, TESTS, COMMON, [], {"uat": {"T1": "failed"}})
    text = mermaid(m)
    assert text.startswith("flowchart LR") and ":::untested" in text and ":::failing" in text
    assert "-->|3|" in text  # Login to Home in three test cases
    small = mermaid(m, limit=3)
    nodes = [ln for ln in small.splitlines() if ":::" in ln and not ln.startswith("classDef")]
    assert len(nodes) == 3 and all(":::failing" in n or ":::untested" in n for n in nodes)
    assert 'Bad"Name' not in mermaid(build("S", {'Bad"Name': "x.json"}, {}, {}, [], {}))
