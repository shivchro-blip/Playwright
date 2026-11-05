"""Ranking replacements for broken locators (no browser: candidates are given)."""
from __future__ import annotations

from webapp.services.locator_suggest import Suggestions, rank, signature, suggest, words
from webapp.services.page_scan import Candidate, PageScan

CANDIDATES = [
    Candidate("ApplicantZipCode", "text", {"locator": "#applicantZipCode"}, "Zip code", "input"),
    Candidate("ContactPhone", "text", {"locator": "#contactPhone"}, "Contact phone", "input"),
    Candidate("NotesBox", "text", {"locator": "#notes-box"}, "Notes", "textarea"),
    Candidate("SubmitApplication", "button", {"role": "button", "name": "Submit application"},
              "Submit application", "button"),
]


def best(name, cfg, **kw):
    found = rank(name, cfg, CANDIDATES, **kw)
    return found[0] if found else None


def test_words_split_camel_case_and_drop_noise():
    assert words("searchBox") == ["search", "box"]
    assert words("#zip-code") == ["zip", "code"]
    assert words("input[name='tax_not_paid']") == ["tax", "not", "paid"]
    assert words("btn-1 the") == []


def test_a_renamed_id_is_found_from_the_old_locator_and_name():
    zip_code = best("zip", {"locator": "#zip-code", "type": "text"})
    assert zip_code.cfg == {"locator": "#applicantZipCode"} and zip_code.score >= 80
    assert "shares" in zip_code.reason and "same type" in zip_code.reason
    phone = best("phone", {"locator": "#phone"})
    assert phone.cfg == {"locator": "#contactPhone"}
    notes = best("notes", {"locator": "#notes"})
    assert notes.cfg == {"locator": "#notes-box"}


def test_the_label_text_still_matches_when_the_locator_is_gone():
    found = best("Submit", {"role": "button", "name": "Submit"})
    assert found.cfg["name"] == "Submit application"
    unrelated = rank("birthDate", {"locator": "#dob"}, CANDIDATES)
    assert unrelated == []  # nothing on the page looks like it


def test_elements_a_working_field_already_uses_are_not_offered():
    taken = {signature({"locator": "#applicantZipCode"})}
    assert best("zip", {"locator": "#zip-code"}, taken=taken) is None


def test_at_most_three_scored_suggestions_best_first():
    many = [Candidate(f"Address{i}", "text", {"locator": f"#address{i}"}, "Address line", "input") for i in range(6)]
    found = rank("addressLine", {"locator": "#address"}, many)
    assert len(found) == 3 and found[0].score >= found[1].score >= found[2].score and found[0].score <= 99


def test_suggest_reports_a_scan_error_and_otherwise_ranks_each_broken_field():
    def failing(url, **kw):
        return PageScan(url=url, error="net::ERR_CONNECTION_REFUSED")

    def scanning(url, **kw):
        assert kw["visible_only"] is False
        return PageScan(url=url, ok=True, candidates=CANDIDATES)

    assert suggest("http://x", {"zip": {}}, scan=failing).error == "net::ERR_CONNECTION_REFUSED"
    result = suggest("http://x", {"zip": {"locator": "#zip-code"}, "dob": {"locator": "#dob"}}, scan=scanning)
    assert isinstance(result, Suggestions) and result.ok and result.scanned == 4
    assert result.by_field["zip"][0].cfg == {"locator": "#applicantZipCode"} and result.by_field["dob"] == []
