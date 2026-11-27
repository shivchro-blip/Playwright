"""Yes/No pairs: one control for two fields, and "add all page fields" adds one answer per question."""
from webapp.services.field_groups import choice_groups, fields_to_add, split_option


def test_split_option():
    assert split_option("ChildSupport_No") == ("ChildSupport", "No")
    assert split_option("ChildSupport_Yes") == ("ChildSupport", "Yes")
    assert split_option("Yes") == ("Yes", "") and split_option("_No") == ("_No", "")
    assert split_option("Notes") == ("Notes", "")


def test_only_a_base_with_both_answers_is_a_group():
    groups = choice_groups(["A_Yes", "A_No", "B_Yes", "C", "D_No", "D_Yes"])
    assert groups == {"A": {"Yes": "A_Yes", "No": "A_No"}, "D": {"No": "D_No", "Yes": "D_Yes"}}


def test_add_all_adds_one_answer_per_question_and_every_other_field():
    names = ["Name", "Franchise_Yes", "Franchise_No", "Email", "Prep_Yes", "Prep_No", "Lone_Yes"]
    assert fields_to_add(names) == ["Name", "Franchise_No", "Email", "Prep_No", "Lone_Yes"]


def test_a_question_already_on_the_step_is_not_asked_again_whichever_answer_it_has():
    names = ["Franchise_Yes", "Franchise_No", "Email"]
    assert fields_to_add(names, {"Franchise_Yes"}) == ["Email"]
    assert fields_to_add(names, {"Franchise_No", "Email"}) == []


def test_nothing_changes_for_a_page_without_yes_no_pairs():
    names = ["a", "b", "c"]
    assert fields_to_add(names, {"b"}) == ["a", "c"]
