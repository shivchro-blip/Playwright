"""Shared page components: the engine's expansion, and how the app reads, validates, refactors and heals them."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from utils import page_components as pc
from webapp.services.heals import HealService
from webapp.services.json_files import ConflictError
from webapp.services.locator_check import build_locator
from webapp.services.pom_service import PomService
from webapp.services.refactor import Refactor, RefactorError, apply_plan
from webapp.services.run_manager import RunManager
from webapp.services.test_case_service import TestCaseService as CaseService
from webapp.services.tidy import analyze
from webapp.services.workspace_model import WorkspaceModel

ADDRESS = {"component": True, "fields": {"street": {"locator": "input.street", "type": "text"},
                                         "city": {"locator": "input.city", "type": "text"}}}
HEADER = {"component": True, "use": ["Nav"], "fields": {"logo": {"role": "img", "type": "button"}}}
NAV = {"component": True, "fields": {"home": {"role": "link", "name": "Home", "type": "link"}}}
APPLY = {"path": "/apply", "use": ["Header", {"component": "Address", "prefix": "mailing", "within": "#mailing"},
                                   {"component": "Address", "prefix": "home"}],
         "fields": {"submit": {"role": "button", "name": "Submit", "type": "button"},
                    "home.city": {"locator": "#own-city", "type": "text"}}}


def w(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load(root: Path, *parts: str) -> dict:
    return json.loads(root.joinpath(*parts).read_text(encoding="utf-8"))


# ───── the engine ─────

def test_expand_puts_component_fields_first_with_prefix_and_container():
    pages = {"Address": ADDRESS, "Header": HEADER, "Nav": NAV}
    origins: dict = {}
    fields = pc.expand_fields(APPLY, pages.get, origins)
    assert list(fields) == ["home", "logo", "mailing.street", "mailing.city", "home.street", "home.city", "submit"]
    assert fields["mailing.street"] == {"locator": "input.street", "type": "text", "within": "#mailing"}
    assert "within" not in fields["home.street"]
    assert fields["home.city"] == {"locator": "#own-city", "type": "text"}  # the page's own replaces it
    assert origins == {"home": "Header", "logo": "Header", "mailing.street": "Address", "mailing.city": "Address",
                       "home.street": "Address"}
    assert pc.expand(APPLY, pages.get)["path"] == "/apply" and APPLY["fields"].keys() == {"submit", "home.city"}


def test_pages_without_use_are_returned_as_they_are():
    page = {"fields": {"a": {"locator": "#a"}}}
    assert pc.expand(page, lambda n: None) is page
    assert pc.expand_fields(page, lambda n: None) == page["fields"]


def test_within_nests_inside_a_components_own_container():
    assert pc.scoped({"locator": "x", "within": ".inner"}, "#outer")["within"] == "#outer >> .inner"
    assert pc.scoped({"locator": "x"}, "")["locator"] == "x"


@pytest.mark.parametrize("page, pages, message", [
    ({"use": ["Missing"]}, {}, "not found"),
    ({"use": ["Plain"]}, {"Plain": {"fields": {}}}, "is a page, not a component"),
    ({"use": ["A", "A"]}, {"A": ADDRESS}, "comes from two components"),
    ({"use": ["Loop"]}, {"Loop": {"component": True, "use": ["Loop"], "fields": {}}}, "uses itself"),
    ({"use": "Address"}, {}, "must be a list"),
    ({"use": [{"prefix": "x"}]}, {}, "names a component"),
    ({"use": [{"component": "A", "prefix": "a|b"}]}, {"A": ADDRESS}, "no '|'"),
])
def test_what_cannot_be_expanded_says_why(page, pages, message):
    with pytest.raises(pc.ComponentError, match=message):
        pc.expand_fields(page, pages.get)


def test_file_lookup_reads_each_file_once(tmp_path):
    w(tmp_path / "components" / "Address.json", ADDRESS)
    paths = pc.folder_paths(str(tmp_path))
    lookup = pc.file_lookup(paths)
    assert lookup("Address") == ADDRESS and lookup("Nope") is None
    (tmp_path / "components" / "Address.json").unlink()
    assert lookup("Address") == ADDRESS  # cached


def test_build_locator_looks_inside_the_container():
    class Fake:
        def __init__(self, chain="page"):
            self.chain = chain

        @property
        def first(self):
            return Fake(self.chain + ".first")

        def __getattr__(self, name):
            return lambda *a, **k: Fake(f"{self.chain}.{name}({', '.join(repr(x) for x in a)})")

    el, desc = build_locator(Fake(), {"locator": "input.street", "within": "#mailing"})
    assert el.chain == "page.locator('#mailing').first.locator('input.street')"
    assert desc == "page.locator('#mailing').first.locator('input.street')"


# ───── the app ─────

@pytest.fixture
def ws(tmp_path: Path):
    pages = tmp_path / "Pages" / "SYS"
    w(pages / "components" / "Address.json", ADDRESS)
    w(pages / "components" / "Header.json", HEADER)
    w(pages / "components" / "Nav.json", NAV)
    w(pages / "Apply.json", APPLY)
    w(pages / "Plain.json", {"fields": {"q": {"locator": "#q", "type": "text"}}})
    w(tmp_path / "Tests" / "SYS" / "MasterRegressionSuite" / "T.json", {"testCases": [
        {"testCaseID": "TC-1", "system": "SYS", "tags": ["t"], "steps": [
            {"screen": "Apply", "formData": {"mailing.street": "1 Main", "home.street": "2 Side", "submit": "click",
                                             "logo": "click"}},
            {"commonAction": "X", "override": "Apply|mailing.city|Albany"}]}]})
    pom = PomService(tmp_path, tmp_path)
    return tmp_path, pom, CaseService(tmp_path, pom)


def test_pom_lists_components_and_reads_pages_as_the_runner_does(ws):
    _, pom, _ = ws
    refs = {r.name: r for r in pom.list_pages("SYS")}
    assert refs["Address"].component and not refs["Apply"].component
    assert refs["Apply"].uses == ("Header", "Address", "Address") and refs["Apply"].fields == 2
    assert [r.name for r in pom.components("SYS")] == ["Address", "Header", "Nav"]
    assert "mailing.city" in pom.field_configs("SYS", "Apply") and "home" in pom.field_names("SYS", "Apply")
    assert pom.field_origins("SYS", "Apply")["mailing.city"] == "Address"
    assert sorted(pom.users_of("SYS", "Address")) == ["Apply"] and pom.users_of("SYS", "Nav") == ["Header"]


def test_field_home_finds_where_a_field_is_defined(ws):
    _, pom, _ = ws
    assert pom.field_home("SYS", "Apply", "mailing.city") == ("Address", "city")
    assert pom.field_home("SYS", "Apply", "home") == ("Nav", "home")  # through Header
    assert pom.field_home("SYS", "Apply", "home.city") == ("Apply", "home.city")  # its own
    assert pom.field_home("SYS", "Apply", "nope") is None
    aliases = pom.aliases("SYS", "Address")
    assert sorted(aliases["city"]) == [("Address", "city"), ("Apply", "mailing.city")]  # home.city is Apply's own
    assert sorted(aliases["street"]) == [("Address", "street"), ("Apply", "home.street"), ("Apply", "mailing.street")]


def test_validation_knows_component_fields(ws):
    _, _, tests = ws
    case = {"testCaseID": "T", "tags": ["t"], "steps": [{"screen": "Apply", "formData": {
        "mailing.street": "x", "home": "click", "mailing.zip": "y"}}]}
    warnings = tests.validate("SYS", case).warnings
    assert warnings == ["Step 1: field 'mailing.zip' is not on page 'Apply'."]


def test_saving_a_page_checks_its_components(ws):
    _, pom, _ = ws
    data, sha = pom.load("SYS", "Plain.json")
    with pytest.raises(ValueError, match="not found"):
        pom.save("SYS", "Plain.json", {**data, "use": ["Nope"]}, sha)
    with pytest.raises(ValueError, match="is a page, not a component"):
        pom.save("SYS", "Plain.json", {**data, "use": ["Apply"]}, sha)
    comp, csha = pom.load("SYS", "components/Nav.json")
    with pytest.raises(ValueError, match="uses itself"):  # Nav > Header > Nav
        pom.save("SYS", "components/Nav.json", {**comp, "use": ["Header"]}, csha)
    with pytest.raises(ValueError, match='"component" is true'):
        pom.save("SYS", "Plain.json", {**data, "component": "yes"}, sha)
    pom.save("SYS", "Plain.json", {**data, "use": [{"component": "Address", "prefix": "a"}]}, sha)
    assert "a.city" in pom.field_configs("SYS", "Plain")


def test_workspace_model_sees_component_fields(ws):
    root, _, tests = ws
    snap = WorkspaceModel(root, tests).snapshot()
    assert {"mailing.street", "home", "submit", "home.city"} <= snap.pages[("SYS", "Apply")]
    assert snap.pages[("SYS", "Address")] == {"street", "city"}


def test_renaming_a_component_field_renames_it_on_every_page_that_uses_it(ws):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.rename_field("SYS", "Address", "city", "town")
    apply_plan(r.plan())
    assert list(load(root, "Pages", "SYS", "components", "Address.json")["fields"]) == ["street", "town"]
    case = load(root, "Tests", "SYS", "MasterRegressionSuite", "T.json")["testCases"][0]
    assert case["steps"][1]["override"] == "Apply|mailing.town|Albany"
    assert load(root, "Pages", "SYS", "Apply.json")["fields"].keys() == {"submit", "home.city"}  # its own stays


def test_a_component_field_cannot_be_renamed_on_the_page(ws):
    _, pom, tests = ws
    with pytest.raises(RefactorError, match="comes from the shared component Address"):
        Refactor(pom, tests).rename_field("SYS", "Apply", "mailing.city", "x")


def test_removing_a_component_field_still_used_by_a_page_is_refused(ws):
    _, pom, tests = ws
    with pytest.raises(RefactorError, match="mailing.street still used"):
        Refactor(pom, tests).remove_fields("SYS", "Address", ["street"])
    with pytest.raises(RefactorError, match="still used"):  # through the override
        Refactor(pom, tests).remove_fields("SYS", "Address", ["city"])


def test_renaming_a_component_updates_the_pages_that_use_it(ws):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.rename_page("SYS", "Address", "PostalAddress")
    apply_plan(r.plan())
    assert load(root, "Pages", "SYS", "Apply.json")["use"][1:] == [
        {"component": "PostalAddress", "prefix": "mailing", "within": "#mailing"},
        {"component": "PostalAddress", "prefix": "home"}]
    assert "mailing.city" in pom.field_configs("SYS", "Apply")


def test_moving_fields_into_a_component_keeps_their_names(ws):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.extract_component("SYS", "Apply", ["submit"], "Actions")
    plan = r.plan()
    assert sorted(plan.summary()) == ["Create Pages/SYS/components/Actions.json", "Pages/SYS/Apply.json: 1 change"]
    before = dict(pom.field_configs("SYS", "Apply"))
    apply_plan(plan)
    assert load(root, "Pages", "SYS", "components", "Actions.json") == {
        "component": True, "fields": {"submit": APPLY["fields"]["submit"]}}
    page = load(root, "Pages", "SYS", "Apply.json")
    assert list(page) == ["path", "use", "fields"] and page["use"][-1] == "Actions"
    assert pom.field_configs("SYS", "Apply").keys() == before.keys()  # the same fields, the tests untouched
    with pytest.raises(RefactorError, match="already exists"):
        Refactor(pom, tests).extract_component("SYS", "Apply", ["home.city"], "Actions")


def test_a_plan_that_creates_a_file_is_undone_when_it_fails(ws, monkeypatch):
    root, pom, tests = ws
    r = Refactor(pom, tests)
    r.extract_component("SYS", "Plain", ["q"], "Search")
    plan = r.plan()
    w(root / "Pages" / "SYS" / "components" / "Search.json", {"component": True, "fields": {}})
    with pytest.raises(ConflictError, match="exists now"):
        apply_plan(plan)
    assert load(root, "Pages", "SYS", "Plain.json")["fields"] == {"q": {"locator": "#q", "type": "text"}}


def test_tidy_counts_a_component_field_used_through_a_page(ws):
    _, pom, tests = ws
    findings = {f.kind: f for f in analyze(pom, tests, "SYS", "Address")}
    assert "unused" not in findings  # street and city are used as mailing.* on Apply
    header = {f.kind: f for f in analyze(pom, tests, "SYS", "Header")}
    assert "unused" not in header  # logo is used on Apply
    nav = {f.kind: f for f in analyze(pom, tests, "SYS", "Nav")}
    assert nav["unused"].names == ["home"]  # on Apply through Header, but no step uses it
    apply = {f.kind: f for f in analyze(pom, tests, "SYS", "Apply")}
    assert apply["unused"].names == ["home.city"]  # its own fields only; the components' are tidied there


def test_accepting_a_heal_of_a_component_field_fixes_the_component(ws):
    root, pom, _ = ws
    rid = "r1"
    from webapp.services.run_manager import Run, RunConfig, TestResult
    run = Run(id=rid, config=RunConfig(system="SYS", env="dev", tags=["t"]), dir=root / "runs" / rid,
              status="passed", created_at="2026-11-08T10:00:00")
    run.results_dir.mkdir(parents=True)
    run.results["n"] = TestResult("n", "TC-1", "chromium", "passed", 1.0)
    heal = {"system": "SYS", "screen": "Apply", "field": "mailing.city", "test_id": "TC-1",
            "old": {"locator": "input.city"}, "new": {"locator": "input[name=city]"}, "score": 95, "reason": "r"}
    (run.results_dir / "autoplay-heals-1.jsonl").write_text(json.dumps(heal) + "\n")
    (run.dir / "run.json").write_text(json.dumps(run.to_dict()))
    (root / "Data").mkdir(exist_ok=True)
    manager = RunManager(repo_root=root, workspace=root, runs_dir=root / "runs", python_exe=sys.executable)
    service = HealService(root, manager, pom)
    (item,) = service.pending()
    service.accept(item.key, "ana")
    assert load(root, "Pages", "SYS", "components", "Address.json")["fields"]["city"] == {
        "type": "text", "locator": "input[name=city]"}
    assert "within" not in load(root, "Pages", "SYS", "components", "Address.json")["fields"]["city"]
    assert service.pending() == []  # already applied, through the component
