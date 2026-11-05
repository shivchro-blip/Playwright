"""Masking of SSNs, card numbers and custom patterns in logs, messages and failure details."""
from __future__ import annotations

import json

import pytest

from webapp.services.masking import Masker, luhn
from webapp.services.run_artifacts import RunArtifacts
from webapp.services.run_manager import Run, RunConfig


def masker(tmp_path, config=None) -> Masker:
    path = tmp_path / "maskPatterns.json"
    if config is not None:
        path.write_text(json.dumps(config), encoding="utf-8")
    return Masker(path)


def test_default_masks_ssns_and_valid_cards_only(tmp_path):
    m = masker(tmp_path)
    assert m.mask("ssn 123-45-6789 ok") == "ssn ***-**-**** ok"
    assert m.mask("card 4111111111111111 and 4111 1111 1111 1111 and 4111-1111-1111-1111") == \
        "card [card ****1111] and [card ****1111] and [card ****1111]"
    assert m.mask("amex 378282246310005 / 3782 822463 10005") == "amex [card ****0005] / [card ****0005]"
    assert luhn("4111111111111111") and not luhn("4111111111111112")


@pytest.mark.parametrize("text", [
    "run 20261105-130055-8e12 finished",          # run id with dashes
    "stamp 20261105130055 taken",                  # 14 digits that are not a card prefix
    "card 4111111111111112",                       # fails the Luhn check
    "id 1234-56-78901",                            # not the SSN shape
    "port 8080, 12 tests, 1500.5s",
    "zip 78701 and 123456789",                     # 9 digits are only masked when routing is on
])
def test_ordinary_text_is_left_alone(tmp_path, text):
    assert masker(tmp_path).mask(text) == text


def test_routing_numbers_are_opt_in_and_checked(tmp_path):
    assert masker(tmp_path).mask("routing 021000021") == "routing 021000021"
    m = masker(tmp_path, {"builtin": ["routing"]})
    assert m.mask("routing 021000021 x") == "routing [routing *****0021] x"
    assert m.mask("not 123456789") == "not 123456789"  # fails the checksum
    assert m.mask("ssn 123-45-6789") == "ssn 123-45-6789"  # only the listed rules run


def test_custom_patterns_and_an_empty_builtin_list(tmp_path):
    m = masker(tmp_path, {"builtin": [], "custom": [
        {"name": "policy", "pattern": r"POL-\d{6}", "replacement": "POL-******"},
        {"name": "email", "pattern": r"[\w.]+@[\w.]+"}]})
    assert m.mask("POL-123456 for a.b@x.org; ssn 123-45-6789") == "POL-****** for [masked email]; ssn 123-45-6789"


def test_a_bad_file_falls_back_to_the_defaults_and_edits_are_picked_up(tmp_path):
    path = tmp_path / "maskPatterns.json"
    path.write_text("{not json", encoding="utf-8")
    m = Masker(path)
    assert m.mask("123-45-6789") == "***-**-****"
    path.write_text(json.dumps({"builtin": ["bogus"]}), encoding="utf-8")
    m.mask("x")
    assert m.mask("123-45-6789") == "***-**-****"
    path.write_text(json.dumps({"builtin": [], "custom": [{"pattern": "(unclosed"}]}), encoding="utf-8")
    assert m.mask("123-45-6789") == "***-**-****"
    path.write_text(json.dumps({"builtin": [], "custom": [{"name": "n", "pattern": "x+"}]}), encoding="utf-8")
    assert m.mask("axxb 123-45-6789") == "a[masked n]b 123-45-6789"


def test_run_scrub_hides_typed_secrets_and_sensitive_values(tmp_path):
    run = Run(id="r", config=RunConfig(system="S", env="e", tags=["t"]), dir=tmp_path)
    run._secrets = {"PW": "hunter22"}
    run._masker = masker(tmp_path)
    assert run.scrub("pw hunter22 ssn 123-45-6789") == "pw *** ssn ***-**-****"
    assert Run(id="r", config=run.config, dir=tmp_path).scrub("ssn 123-45-6789") == "ssn 123-45-6789"


def test_failure_details_are_masked_when_shown(tmp_path):
    results = tmp_path / "allure-results"
    results.mkdir()
    (results / "aaaaaaaa-0000-attachment.txt").write_text("typed 123-45-6789\n", encoding="utf-8")
    (results / "bbbbbbbb-0000-attachment.txt").write_text("card 4111111111111111", encoding="utf-8")
    result = {"name": "TC-1 [chromium]", "status": "failed",
              "statusDetails": {"message": "expected 123-45-6789", "trace": "at ssn 123-45-6789"},
              "attachments": [{"name": "log", "source": "aaaaaaaa-0000-attachment.txt", "type": "text/plain"},
                              {"name": "note", "source": "bbbbbbbb-0000-attachment.txt", "type": "text/plain"}],
              "steps": [{"name": "Screen: Applicant", "status": "failed", "steps": [
                  {"name": "Fill SSN with 123-45-6789", "status": "failed"}]}]}
    (results / "a-result.json").write_text(json.dumps(result), encoding="utf-8")
    shown = RunArtifacts(mask=masker(tmp_path).mask).details("r", results)[("TC-1", "chromium")]
    assert shown.message == "expected ***-**-****" and shown.trace == "at ssn ***-**-****"
    assert shown.log == "typed ***-**-****\n"
    assert shown.by_kind("text")[0].text == "card [card ****1111]"
    assert "123-45-6789" not in repr(shown)
    plain = RunArtifacts().details("r2", results)[("TC-1", "chromium")]
    assert plain.message == "expected 123-45-6789"
