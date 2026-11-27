"""Page from HTML: real pages of the OPA site (tests/unit/fixtures/opa_pages) must give what a person wrote by hand."""
import json
from pathlib import Path

import pytest

from webapp.services import html_page as hp
from webapp.services import page_conventions as pc

FIXTURES = Path(__file__).parent / "fixtures" / "opa_pages"


def page(name: str) -> hp.PageDraft:
    return hp.build((FIXTURES / f"{name}.html").read_text(encoding="utf-8"))


def by_name(draft: hp.PageDraft) -> dict[str, hp.FieldDraft]:
    return {f.name: f for f in draft.fields}


# ───────────── the locators and settings a person wrote by hand ─────────────

def test_yes_no_questions_are_two_label_clicks_named_from_the_sites_id():
    fields = by_name(page("applicant_statements"))
    assert fields["AttestOwedChildSupport_Yes"].cfg == {
        "locator": "[data-attributeid='LSI_AEB_AttestOwedChildSupport'] label[for^='boolRadioY_']", "type": "click"}
    assert fields["AttestOwedChildSupport_No"].cfg == {
        "locator": "[data-attributeid='LSI_AEB_AttestOwedChildSupport'] label[for^='boolRadioN_']", "type": "click"}


def test_checkboxes_are_clicked_through_their_label():
    fields = by_name(page("applicant_statements"))
    assert fields["AttestAEApplicant"].cfg == {
        "locator": "[data-attributeid='LSI_AEB_AttestAEApplicant'] label.checkbox_label", "type": "click"}


def test_a_date_picker_is_typed_key_by_key_and_gets_a_date_rule():
    cfg = by_name(page("business_associations"))["PreparerDate"].cfg
    assert cfg == {"locator": "[data-attributeid='LSI_DTF17_PreparerDate'] input", "type": "text", "typeKeys": True,
                   "autoGen": "getDateBetween(-30, 0, '%m/%d/%Y')"}


def test_a_split_ssn_is_three_fields_typed_key_by_key():
    fields = by_name(page("individual_information"))
    area, group, serial = (fields[f"IndiviSSN{n}"].cfg for n in ("Area", "Group", "Serial"))
    assert area == {"locator": "[data-attributeid='CDI_IndiviSSN'] input[name$='-ssn1']", "type": "text",
                    "typeKeys": True, "autoGen": "getIntBetween(101, 666)"}
    assert group["locator"].endswith("input[name$='-ssn2']") and group["autoGen"] == "getIntBetween(11, 99)"
    assert serial["locator"].endswith("input[name$='-ssn3']") and serial["autoGen"] == "getIntBetween(1001, 9999)"


def test_a_date_in_three_selects_is_told_apart_by_each_selects_own_class():
    fields = by_name(page("individual_information"))
    assert fields["IndiviDOBYear"].cfg == {"locator": "[data-attributeid='CDI_IndiviDOB'] select.dmyyear",
                                           "type": "select", "autoGen": "getIntBetween(1960, 2006)"}
    assert fields["IndiviDOBMonth"].cfg["locator"].endswith("select.dmymonth")
    assert fields["IndiviDOBDay"].cfg["locator"].endswith("select.dmyday")


def test_an_upload_is_the_visible_choose_a_file_label_and_its_hidden_helpers_are_not_fields():
    fields = by_name(page("experience"))
    assert fields["ExperienceDocUpload"].cfg == {
        "locator": "[data-attributeid='LSI_AEB_ExperienceDoc_Upload'] label.fileUpload_button",
        "type": "fileChooserDocument", "autoGen": "document"}
    assert not [n for n in fields if n.startswith("ExperienceDocUpload") and n != "ExperienceDocUpload"]


def test_radio_options_are_one_field_each_found_by_their_value():
    fields = by_name(page("experience"))
    assert fields["ExperienceType_WithinTheUnitedStates"].cfg == {
        "locator": "[data-attributeid='LSI_AEB_ExperienceType'] input[value='EXP_INUSA'] + label.radio_label",
        "type": "click"}
    assert fields["ExperienceType_OutsideTheUnitedStates"].kind == "option"


def test_a_table_of_records_is_one_field_that_picks_a_row_by_its_text():
    cfg = by_name(page("attestation_dtf17"))["AttestationID"].cfg
    assert cfg == {"locator": "[data-attributeid='LSI_DTF17_Attestation_ID'] "
                              "tr:has(label:has-text('{value}')) label.radio_label", "type": "click"}


def test_autogen_rules_come_from_the_label_and_the_placeholder():
    fields = by_name(page("business_associations"))
    assert fields["TaxPreparerEmail"].cfg["autoGen"] == "email"
    assert fields["TaxPreparerPhone"].cfg["autoGen"] == "phone"
    assert fields["FranAdd1"].cfg["autoGen"] == "addressLine1"
    assert fields["FranAddCity"].cfg["autoGen"] == "city"
    assert fields["FranName"].cfg["autoGen"] == "legalNameInc"
    assert fields["FranIDNum"].cfg["autoGen"] == "getIntBetween(100000000, 1000000000)"  # no hyphen: digits only
    assert fields["TaxPayersEin"].cfg["autoGen"] == "getIntBetween(100000000, 1000000000)"  # placeholder #########


def test_an_ein_with_a_hyphenated_placeholder_gets_the_hyphenated_rule():
    html = ('<div data-attributeid="LSI_AB_Ein" class="rn_OpaControl required"><label for="e">EIN</label>'
            '<input id="e" type="text" name="e" placeholder="##-#######"></div>')
    assert hp.build(html).fields[0].cfg["autoGen"] == "fein"


def test_an_optional_text_box_with_no_matching_rule_gets_no_rule():
    assert "autoGen" not in by_name(page("individual_information"))["IndiviMiddleInitial"].cfg


def test_selects_keep_their_options_and_skip_the_placeholder_ones():
    select = by_name(page("experience"))["ExperienceState"]
    assert [v for v, _ in select.options] == ["NY", "AL", "CA"]
    dob = by_name(page("individual_information"))["IndiviDOBYear"]
    assert dob.options == [("2026", "2026")]  # "YYYY" is a prompt, not a choice


# ───────────── the page name, the button, the notes ─────────────

def test_the_page_name_comes_from_the_heading_and_the_button_from_the_button_container():
    d = page("experience")
    assert d.page_name == "Experience" and d.common_action == "Save and continue"
    assert page("business_associations").page_name == "BusinessAssociations"


def test_a_page_without_buttons_says_so_and_uses_the_default_action():
    d = page("applicant_statements")
    assert d.common_action == "Save and continue"
    assert any("no buttons" in n for n in d.notes)


def test_the_next_button_is_found_when_that_is_the_one_that_moves_on():
    html = ('<div data-attributeid="A" class="rn_OpaControl"><input type="text" name="a"></div>'
            '<div id="button-container"><a id="btnBack" class="btn">Back</a>'
            '<input id="btnNext" class="btn btn-primary" type="button" value="Next"></div>')
    d = hp.build(html)
    assert d.common_action == "Next"
    assert d.notes == []  # nothing left to decide except that the field has no rule (it is optional)


def test_what_the_page_hid_is_named_with_the_question_that_probably_reveals_it():
    assert page("business_associations").reveals == {
        "Franchise": ["FranName", "FranIDNum", "FranAdd1", "FranAddCity"],
        "HasTaxPrep": ["TaxPayersEin", "PreparerDate", "TaxPreparerEmail", "TaxPreparerPhone"]}
    # the questions in one hidden block are revealed together, not by each other
    assert page("applicant_statements").reveals == {
        "AttestOwedChildSupport": ["AttestOwedChildSupport4orMoreMonths", "AttestChildSupportPaymentPlan"]}


def test_required_fields_no_rule_fits_are_listed_not_guessed():
    html = ('<div data-attributeid="LSI_AB_Widget" class="rn_OpaControl required"><label for="w">Widget colour</label>'
            '<input id="w" type="text" name="w" aria-required="true"></div>')
    d = hp.build(html)
    assert d.form_data() == {}  # nothing to fill it with
    assert any("Widget" in n and "no autoGen" in n for n in d.notes)


def test_a_list_with_add_new_links_is_reported_not_read():
    html = ('<div data-attributeid="LSI_AB_List" class="rn_OpaControl"><a class="btn add-new-instance" href="#">'
            'Add New</a></div>'
            '<div data-attributeid="LSI_AB_Name" class="rn_OpaControl"><input type="text" name="n"></div>')
    assert any("Add New" in n for n in hp.build(html).notes)


# ───────────── the steps ─────────────

def test_the_main_step_answers_no_and_fills_what_the_page_requires_and_shows():
    d = page("business_associations")
    assert d.steps() == [{"screen": "BusinessAssociations",
                          "formData": {"Franchise_No": "click", "HasTaxPrep_No": "click"}},
                         {"commonAction": "Save and continue"}]


def test_the_yes_step_answers_yes_and_fills_what_that_reveals_in_page_order():
    data = page("business_associations").form_data(prefer="Yes", include_hidden=True)
    assert list(data) == ["Franchise_Yes", "FranName", "FranIDNum", "FranAdd1", "FranAddCity", "HasTaxPrep_Yes",
                          "TaxPayersEin", "PreparerDate", "TaxPreparerEmail", "TaxPreparerPhone"]
    assert data["Franchise_Yes"] == "click" and data["FranName"] is None


def test_a_question_is_answered_once_even_when_both_answers_are_chosen():
    d = page("applicant_statements")
    data = d.form_data({"AttestOwedChildSupport_Yes", "AttestOwedChildSupport_No"}, prefer="No")
    assert data == {"AttestOwedChildSupport_Yes": "click"}  # the first in page order wins; the toggle picks one


def test_chosen_fields_are_used_as_they_are_including_optional_ones():
    d = page("applicant_statements")
    data = d.form_data({"AttestOwedChildSupport_No", "AttestAEApplicant"})
    assert data == {"AttestOwedChildSupport_No": "click", "AttestAEApplicant": "click"}


def test_a_select_without_a_rule_takes_its_first_option_and_a_preselected_one_is_left_alone():
    html = ('<div data-attributeid="LSI_AB_State" class="rn_OpaControl required"><label for="s">State</label>'
            '<select id="s" name="s"><option value="">- Select One -</option><option value="NY" selected>New York'
            '</option><option value="CA">California</option></select></div>'
            '<div data-attributeid="LSI_AB_Kind" class="rn_OpaControl required"><label for="k">Kind</label>'
            '<select id="k" name="k"><option value="">- Select One -</option>'
            '<option value="A">A</option></select></div>')
    d = hp.build(html)
    assert d.form_data() == {"Kind": "A"}
    assert any("first option: Kind" in n for n in d.notes)


def test_the_page_json_has_only_what_the_framework_reads():
    fields = page("individual_information").page_json()["fields"]
    assert set(fields["IndiviSSNArea"]) == {"locator", "type", "typeKeys", "autoGen"}
    only = page("individual_information").page_json(only={"IndiviLastName"})
    assert list(only["fields"]) == ["IndiviLastName"]


# ───────────── pages that are not like that ─────────────

def test_nothing_in_the_html_says_so():
    d = hp.build("<p>nothing to fill in here</p>")
    assert d.fields == [] and any("No fields" in n for n in d.notes)


def test_a_page_with_no_site_id_attribute_is_located_by_name_then_id():
    html = ('<form><label for="q">Search</label><input id="q" name="q" type="text">'
            '<input id="input-83729104" type="text" name="r8d7a6b5c4e3f2a1b0c9d8e7f6a5b4c3d"></form>')
    d = hp.build(html)  # Generic profile: no container
    assert [f.cfg["locator"] for f in d.fields] == ["input[name='q']"]
    assert any("1 control(s)" in n for n in d.notes)  # the one with a generated name and id is left out


def test_duplicate_names_get_a_number():
    html = ('<div data-attributeid="LSI_AB_Name" class="rn_OpaControl"><input type="text" name="a"></div>'
            '<div data-attributeid="CDI_Name" class="rn_OpaControl"><input type="text" name="b"></div>')
    assert [f.name for f in hp.build(html).fields] == ["Name", "Name2"]


def test_untidy_html_is_read_without_failing():
    html = ('<div data-attributeid="LSI_AB_A" class="rn_OpaControl required"><label for=a>A name'
            '<input id=a type=text name=a aria-required=true><div><span>unclosed')
    assert hp.build(html).fields[0].cfg["locator"] == "[data-attributeid='LSI_AB_A'] input"


# ───────────── conventions ─────────────

def test_the_opa_profile_is_chosen_when_the_html_looks_like_the_site():
    assert pc.detect('<div class="rn_OpaControl">')["name"] == "OPA (NYBE)"
    assert pc.detect("<form></form>")["name"] == "Generic"


def test_a_workspace_changes_the_conventions_per_system(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "pageConventions.json").write_text(json.dumps({
        "*": {"required_class": "mandatory"},
        "NYBE": {"autogen_by_label": [["\\bwidget\\b", "word"]]}}), encoding="utf-8")
    profile, notes = pc.load(tmp_path, "NYBE", '<div class="rn_OpaControl">')
    assert notes == [] and profile["required_class"] == "mandatory"
    assert profile["autogen_by_label"] == [["\\bwidget\\b", "word"]]  # a list replaces the built-in list
    assert profile["id_attribute"] == "data-attributeid"  # the rest of the built-in profile is kept
    other, _ = pc.load(tmp_path, "OTHER", '<div class="rn_OpaControl">')
    assert other["required_class"] == "mandatory" and other["autogen_by_label"] != profile["autogen_by_label"]


@pytest.mark.parametrize("content, why", [
    ("{not json", "could not be read"),
    ("[1, 2]", "must be an object"),
    ('{"*": {"autogen_by_label": [["(unclosed", "name"]]}}', "not a valid regular expression"),
    ('{"*": {"autogen_by_label": "no"}}', "must be a list"),
])
def test_a_bad_conventions_file_falls_back_to_the_built_in_profile_and_says_why(tmp_path, content, why):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "pageConventions.json").write_text(content, encoding="utf-8")
    profile, notes = pc.load(tmp_path, "NYBE", '<div class="rn_OpaControl">')
    assert profile["name"] == "OPA (NYBE)" and profile["autogen_by_label"] == pc.GENERIC["autogen_by_label"]
    assert any(why in n for n in notes)


def test_unknown_keys_in_the_conventions_file_are_reported(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "pageConventions.json").write_text('{"NYBE": {"colour": "red"}}', encoding="utf-8")
    _, notes = pc.load(tmp_path, "NYBE", "<form></form>")
    assert any("colour" in n for n in notes)


def test_a_custom_profile_changes_how_a_site_is_read():
    profile = pc.detect("<form></form>")
    profile.update(id_attribute="data-qa", required_class="req")
    html = '<div data-qa="Email" class="req"><label for="e">E-mail</label><input id="e" type="text" name="e"></div>'
    f = hp.build(html, profile).fields[0]
    assert f.cfg == {"locator": "[data-qa='Email'] input", "type": "text", "autoGen": "email"} and f.required


def test_html_nested_thousands_deep_is_refused_with_a_message_not_a_crash():
    html = "<div>" * 5000 + '<input type="text" name="deep">' + "</div>" * 5000
    with pytest.raises(hp.HtmlError, match="nested too deeply"):
        hp.build(html)


def test_the_dialog_renames_fields_in_a_step_without_touching_the_values():
    from webapp.ui.html_page_dialog import renamed
    assert renamed({"A_No": "click", "B": None, "C": "NY"}, {"A_No": "Taxed_No", "B": "Email"}) == \
        {"Taxed_No": "click", "Email": None, "C": "NY"}
