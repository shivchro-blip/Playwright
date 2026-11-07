"""Element identity: fingerprints, similarity, the identity block, the runner's choice, and field health."""
import asyncio
import json
from types import SimpleNamespace

from utils import element_identity as ei
from utils import self_heal
from webapp.services.identity_health import collect, promote

SEARCH = {"tag": "input", "type": "", "id": "q", "name": "q", "label": "Search", "placeholder": "", "text": ""}


def test_fingerprint_keeps_what_is_there():
    assert ei.fingerprint({**SEARCH, "tag": "INPUT", "cls": ["x"], "visible": True}) == \
        {"tag": "input", "id": "q", "name": "q", "label": "Search"}


def test_similarity():
    fp = ei.fingerprint(SEARCH)
    assert ei.similarity(fp, SEARCH) == 100
    assert ei.similarity(fp, {**SEARCH, "id": "query"}) == 67  # id renamed: name and label still match
    assert ei.similarity(fp, {**SEARCH, "tag": "textarea"}) == 0  # another kind of element
    assert ei.similarity(fp, {**SEARCH, "type": "checkbox"}) == 0  # input of another type
    assert ei.similarity(fp, {"tag": "input", "label": " search "}) == 33  # case and spaces do not matter
    assert ei.similarity({}, SEARCH) == 0
    assert ei.similarity({"tag": "button"}, {"tag": "button"}) == 100


def test_make_identity_drops_the_primary_and_duplicates():
    ident = ei.make_identity(SEARCH, {"locator": "#q", "type": "text"},
                             [{"locator": "#q"}, {"locator": "input[name='q']"}, {"label": "Search"},
                              {"locator": "input[name='q']"}, {}], verified="2026-11-07T10:00:00")
    assert ident == {"fingerprint": {"tag": "input", "id": "q", "name": "q", "label": "Search"},
                     "locators": [{"locator": "input[name='q']"}, {"label": "Search"}],
                     "verified": "2026-11-07T10:00:00"}
    assert ei.identity_of({"locator": "#q", "identity": ident}) is ident
    assert ei.identity_of({"locator": "#q"}) == {} and ei.identity_of({"identity": {"locators": []}}) == {}


class FakeLocator:
    def __init__(self, observed):
        self.observed = observed  # list of fingerprints this locator matches
        self.first = self

    async def wait_for(self, state, timeout):
        if not self.observed:
            raise TimeoutError("nothing")

    async def count(self):
        return len(self.observed)

    async def evaluate(self, js):
        return self.observed[0]


def run(element, cfg, page_elements, monkeypatch, tmp_path):
    monkeypatch.setenv("reportsDir", str(tmp_path))
    monkeypatch.setenv("identityWaitMs", "1")
    self_heal.CURRENT.update(system="S", screen="Home", test="TC-1")
    ei._seen.clear()

    async def locate(c):
        return FakeLocator(page_elements.get(json.dumps(c, sort_keys=True), []))
    got = asyncio.run(ei.resolve(element, "searchBox", cfg, locate))
    records = [json.loads(line) for f in tmp_path.glob("autoplay-identity-*.jsonl")
               for line in f.read_text().splitlines()]
    return got, records


def field_cfg():
    return {"locator": "#q", "type": "text",
            "identity": ei.make_identity(SEARCH, {"locator": "#q"},
                                         [{"locator": "input[name='q']"}, {"label": "Search"}])}


def test_the_own_locator_is_used_when_it_works(monkeypatch, tmp_path):
    own = FakeLocator([SEARCH])
    got, records = run(own, field_cfg(), {}, monkeypatch, tmp_path)
    assert got is own and [r["outcome"] for r in records] == ["ok"]


def test_a_fallback_rescues_a_renamed_id(monkeypatch, tmp_path):
    renamed = {**SEARCH, "id": "query"}
    got, records = run(FakeLocator([]), field_cfg(), {
        json.dumps({"locator": "input[name='q']"}): [renamed]}, monkeypatch, tmp_path)
    assert got.observed == [renamed]
    assert records[0]["outcome"] == "fallback" and records[0]["used"] == 0
    assert records[0]["locator"] == {"locator": "input[name='q']"} and records[0]["score"] == 67


def test_a_fallback_that_finds_something_else_is_not_used(monkeypatch, tmp_path):
    other = {"tag": "input", "name": "q", "label": "Other", "id": "x"}  # same name, different element: 33%
    labelled = {**SEARCH, "id": ""}  # its id is gone; its name and label are still there: 67%
    got, records = run(FakeLocator([]), field_cfg(), {
        json.dumps({"locator": "input[name='q']"}): [other],
        json.dumps({"label": "Search"}): [labelled]}, monkeypatch, tmp_path)
    assert got.observed == [labelled] and records[0]["used"] == 1


def test_drift_is_reported_but_the_element_is_kept(monkeypatch, tmp_path):
    own = FakeLocator([{"tag": "input", "id": "q2", "name": "zip", "label": "Postcode"}])
    got, records = run(own, field_cfg(), {}, monkeypatch, tmp_path)
    assert got is own and records[0]["outcome"] == "drift"


def test_nothing_found_is_missing_and_fields_without_identity_are_untouched(monkeypatch, tmp_path):
    none = FakeLocator([])
    got, records = run(none, field_cfg(), {}, monkeypatch, tmp_path)
    assert got is none and records[0] == {**records[0], "outcome": "missing", "tried": 2}
    plain = FakeLocator([])
    got, records = run(plain, {"locator": "#q"}, {}, monkeypatch, tmp_path / "x")
    assert got is plain and records == []


def test_health_and_promote(tmp_path):
    def run_with(name, *outcomes):
        folder = tmp_path / name
        folder.mkdir()
        lines = [json.dumps({"system": "S", "screen": "Home", "field": "searchBox", "outcome": o,
                             "at": f"2026-11-07T10:0{i}:00",
                             **({"locator": {"locator": "input[name='q']"}} if o == "fallback" else {})})
                 for i, o in enumerate(outcomes)]
        (folder / "autoplay-identity-1.jsonl").write_text("\n".join(lines) + "\n")
        return SimpleNamespace(results_dir=folder)
    health = collect([run_with("r2", "fallback", "fallback"), run_with("r1", "ok", "ok")], system="S")
    h = health[("S", "Home", "searchBox")]
    assert h.counts == {"ok": 2, "fallback": 2} and h.score == 75 and h.fallback_cfg == {"locator": "input[name='q']"}
    assert h.summary == "2 ok, 2 fallback"
    assert collect([run_with("r3", "ok")], system="OTHER") == {}

    cfg = field_cfg()
    new = promote(cfg, h.fallback_cfg)
    assert new["locator"] == "input[name='q']" and new["type"] == "text"
    assert new["identity"]["locators"] == [{"locator": "#q"}, {"label": "Search"}]
    assert new["identity"]["fingerprint"] == cfg["identity"]["fingerprint"]


def test_learn_matches_fields_by_their_own_locator_only():
    from webapp.services.identity_health import learn
    ident = ei.make_identity(SEARCH, {"locator": "#q"}, [{"locator": "input[name='q']"}])
    scanned = [SimpleNamespace(cfg={"locator": "#q"}, identity=ident),
               SimpleNamespace(cfg={"locator": "#notes"}, identity=ei.make_identity({"tag": "textarea"},
                                                                                    {"locator": "#notes"}, []))]
    got = {g.field: g for g in learn({"searchBox": {"locator": "#q", "type": "text"},
                                      "missing": {"locator": "#nope", "type": "text"},
                                      "notes": {"locator": "#notes", "type": "text", "identity": ident},
                                      "pageTitle": {"type": "text"}}, scanned)}
    assert got["searchBox"].identity is ident and got["searchBox"].note == "1 fallback locator"
    assert got["missing"].identity == {} and "not found" in got["missing"].note
    assert got["notes"].note == "0 fallback locators (replaces its identity)"
    assert "pageTitle" not in got
