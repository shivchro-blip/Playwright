"""The folder filter and the tags worth showing, and what the catalog and the test list now carry."""
import json

from webapp.services.list_views import ALL_FOLDERS, NO_FOLDER, folder_choices, folder_of, in_folder, picker_tags, \
    visible_tags
from webapp.services.test_case_service import TestCaseService
from webapp.services.test_catalog import TestCatalogService
from webapp.services.pom_service import PomService


def test_folder_of():
    assert folder_of("CE200/TC_1.json") == "CE200" and folder_of("a/b/c.json") == "a/b"
    assert folder_of("TC_1.json") == "" and folder_of("a\\b.json") == "a"


def test_the_filter_offers_each_folder_with_its_count_and_only_when_there_is_a_choice():
    assert folder_choices(["A", "A", "B", ""]) == {ALL_FOLDERS: "All folders (4)", NO_FOLDER: "(top level) (1)",
                                                    "A": "A (2)", "B": "B (1)"}
    assert folder_choices(["A", "A"]) == {} and folder_choices([]) == {}


def test_in_folder():
    assert in_folder("A", ALL_FOLDERS) and in_folder("", ALL_FOLDERS)
    assert in_folder("A", "A") and not in_folder("B", "A")
    assert in_folder("", NO_FOLDER) and not in_folder("A", NO_FOLDER)


def test_a_tag_equal_to_the_tests_own_id_is_not_shown():
    assert visible_tags("TC_1", ["NYBE", "TC_1", "smoke"]) == ["NYBE", "smoke"]
    assert visible_tags("TC_1", ["tc_1"]) == [] and visible_tags("TC_1", ["TC_10"]) == ["TC_10"]


def test_the_picker_drops_only_tags_that_are_just_one_tests_id():
    tests = [("TC_1", ["NYBE", "TC_1"]), ("TC_2", ["NYBE", "TC_2", "TC_1"])]  # TC_1 also tags TC_2: a real group
    assert picker_tags(["NYBE", "TC_1", "TC_2"], tests) == ["NYBE", "TC_1"]
    assert picker_tags([], tests) == []


def _suite(tmp_path):
    suite = tmp_path / "Tests" / "SYS" / "MasterRegressionSuite"
    for folder, n in (("A", 2), ("B", 1)):
        (suite / folder).mkdir(parents=True)
        (suite / folder / "t.json").write_text(json.dumps({"testCases": [
            {"testCaseID": f"TC_{folder}_{i}", "tags": ["SYS", f"TC_{folder}_{i}", folder], "steps": [{"screen": "P"}],
             "description": f"Does {folder} {i}"} for i in range(n)]}), encoding="utf-8")
    return tmp_path


def test_the_catalog_carries_the_description_and_offers_a_tidy_tag_list(tmp_path):
    catalog = TestCatalogService(_suite(tmp_path)).load("SYS")
    assert catalog.tests[0].description == "Does A 0"
    assert catalog.picker_tags() == ["A", "B", "SYS"]  # the three IDs are not offered
    assert "TC_A_0" in catalog.all_tags()  # but selecting by it still works
    assert len(catalog.select(["TC_A_0"])) == 1


def test_the_generators_test_list_carries_the_description(tmp_path):
    refs, errors = TestCaseService(_suite(tmp_path), PomService(tmp_path, tmp_path)).list_tests("SYS")
    assert not errors and [r.description for r in refs] == ["Does A 0", "Does A 1", "Does B 0"]
