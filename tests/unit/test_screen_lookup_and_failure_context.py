"""Screen lookup (one fixed order, duplicates reported) and failure text (names the screen and the action).

A screen is found by file name alone. Two files with one name used to shadow each other, and which one won depended
on the order the OS lists folders, so a test passed on one machine and failed on another with an unrelated message
("No Field Config for: IAgree"). These tests pin the fixed behaviour.
"""
import asyncio
import json
from pathlib import Path

import pytest

from utils import dynamic_form_handler as dfh
from utils import page_components as pc
from webapp.services.pom_service import PomService


def write(path: Path, data=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data if data is not None else {"fields": {}}), encoding="utf-8")


# ───────────── scan_screens ─────────────

def test_scan_finds_every_page_by_file_name_in_any_folder(tmp_path):
    write(tmp_path / "A" / "Home.json")
    write(tmp_path / "B" / "deep" / "Checkout.json")
    (tmp_path / "A" / "notes.txt").write_text("not a page", encoding="utf-8")
    paths, duplicates = pc.scan_screens(str(tmp_path))
    assert sorted(paths) == ["Checkout", "Home"]
    assert duplicates == {}
    assert paths["Checkout"] == str(tmp_path / "B" / "deep" / "Checkout.json")


def test_duplicate_names_are_reported_and_the_last_in_sorted_order_is_used(tmp_path):
    write(tmp_path / "CE200" / "Attestation.json", {"fields": {"IAgree": {}}})
    write(tmp_path / "DTF17" / "Attestation.json", {"fields": {"AttestCheck": {}}})
    write(tmp_path / "DTF17" / "Other.json")
    paths, duplicates = pc.scan_screens(str(tmp_path))
    assert duplicates == {"Attestation": [str(tmp_path / "CE200" / "Attestation.json"),
                                          str(tmp_path / "DTF17" / "Attestation.json")]}
    assert paths["Attestation"] == str(tmp_path / "DTF17" / "Attestation.json")  # last wins, as before


def test_order_does_not_depend_on_how_the_os_lists_folders(tmp_path, monkeypatch):
    write(tmp_path / "a" / "Same.json")
    write(tmp_path / "b" / "Same.json")
    real_walk = pc.os.walk

    def reversed_walk(top):  # a different OS lists the same folders in another order
        for dirpath, dirnames, filenames in real_walk(top):
            dirnames[:] = sorted(dirnames, reverse=True)
            yield dirpath, dirnames, sorted(filenames, reverse=True)

    monkeypatch.setattr(pc.os, "walk", reversed_walk)
    paths, duplicates = pc.scan_screens(str(tmp_path))
    assert paths["Same"] == str(tmp_path / "b" / "Same.json")
    assert duplicates["Same"] == [str(tmp_path / "a" / "Same.json"), str(tmp_path / "b" / "Same.json")]


def test_a_missing_folder_has_no_screens(tmp_path):
    assert pc.scan_screens(str(tmp_path / "nope")) == ({}, {})


def test_folder_paths_keeps_returning_name_to_path(tmp_path):
    write(tmp_path / "A" / "Home.json")
    assert pc.folder_paths(str(tmp_path)) == {"Home": str(tmp_path / "A" / "Home.json")}


def test_duplicate_message_names_the_screen_the_files_and_the_remedy(tmp_path):
    files = [str(tmp_path / "CE200" / "Attestation.json"), str(tmp_path / "DTF17" / "Attestation.json")]
    message = pc.duplicate_message("Attestation", files, str(tmp_path))
    assert "'Attestation'" in message and "2 page files" in message
    assert str(Path("CE200") / "Attestation.json") in message and str(Path("DTF17") / "Attestation.json") in message
    assert "unique" in message and "prefix" in message


def test_the_app_lists_screens_in_the_same_order_as_the_runner(tmp_path):
    write(tmp_path / "Pages" / "SYS" / "CE200" / "Attestation.json")
    write(tmp_path / "Pages" / "SYS" / "DTF17" / "Attestation.json")
    write(tmp_path / "Pages" / "SYS" / "DTF17" / "Home.json")
    index = PomService(tmp_path, tmp_path).screen_index("SYS")
    assert index == {"Attestation": "DTF17/Attestation.json", "Home": "DTF17/Home.json"}


# ───────────── failure context ─────────────

@pytest.fixture(autouse=True)
def clean_context():
    dfh.set_failure_context(screen="")
    yield
    dfh.set_failure_context(screen="")


class FakePage:
    url = "https://example.test/app/interview"


def failure_text(message="boom"):
    with pytest.raises(pytest.fail.Exception) as raised:
        asyncio.run(dfh.addFail(message))
    return str(raised.value)


def test_a_failure_names_the_screen_the_page_and_the_action():
    dfh.set_failure_context(screen="DOSAE_Experience", page=FakePage())
    actions = [{"type": "click", "field": "ExpWithinUsa"},
               {"type": "select", "field": "ExperienceState", "value": "CA"}]
    dfh.set_failure_context(actions=actions)
    dfh.set_failure_context(action=actions[1])
    text = failure_text("could not select")
    assert "[Current Page = DOSAE_Experience https://example.test/app/interview]" in text
    assert "[Current Action = select ExperienceState]" in text
    assert "['click ExpWithinUsa', 'select ExperienceState']]" in text  # the bracket is closed now
    assert "Failure Message: could not select" in text


def test_the_failure_text_never_carries_a_typed_value():
    dfh.set_failure_context(screen="Login", page=FakePage())
    secret = {"type": "fill", "field": "password", "value": "hunter2-do-not-print"}
    dfh.set_failure_context(actions=[secret], action=secret)
    assert "hunter2" not in failure_text()


def test_a_new_screen_clears_the_previous_screens_actions():
    dfh.set_failure_context(screen="First", page=FakePage())
    dfh.set_failure_context(actions=[{"type": "click", "field": "a"}], action={"type": "click", "field": "a"})
    dfh.set_failure_context(screen="Second", page=FakePage())
    text = failure_text()
    assert "Second" in text and "First" not in text
    assert "[Current Action = ]" in text and "[Action List = []]" in text


def test_get_fail_info_returns_what_was_set():
    dfh.set_failure_context(screen="Home", page=FakePage())
    dfh.set_failure_context(actions=[{"type": "click", "field": "go"}], action={"type": "click", "field": "go"})
    assert asyncio.run(dfh.getFailInfo()) == ("Home https://example.test/app/interview", "click go", ["click go"])


# ───────────── "No Field Config" ─────────────

POM = {"AttestingPerson": {"locator": "#a"}, "AttestCheck": {"locator": "#b"}, "AttestText": {"locator": "#c"}}


def test_an_unknown_field_fails_with_the_nearest_names_and_the_screens_fields():
    dfh.set_failure_context(screen="Attestation", page=FakePage())
    with pytest.raises(pytest.fail.Exception) as raised:
        asyncio.run(dfh.generate_action(None, "AttestChek", "click", None, POM))
    text = str(raised.value)
    # the start of the message is what the Allure "Generate Actions Failure" category matches
    assert "Failed to generate Actions - No Field Config for: AttestChek" in text
    assert "Did you mean: AttestCheck" in text
    assert "Fields on this screen: AttestCheck, AttestingPerson, AttestText" in text
    assert "[Current Page = Attestation" in text


def test_the_hint_ignores_a_loop_suffix_and_case():
    assert "Did you mean: AttestCheck" in dfh._field_hint("attestcheck|ELoop(2)", POM)


def test_the_hint_lists_a_long_field_list_in_part_and_says_how_many_there_are():
    many = {f"Field{i:03d}": {} for i in range(60)}
    hint = dfh._field_hint("Nothing", many)
    assert "60 in all" in hint and "Field000" in hint and "Field059" not in hint


def test_the_hint_for_an_empty_screen_says_it_has_no_fields():
    assert "no fields" in dfh._field_hint("x", {})
    assert "no fields" in dfh._field_hint("x", None)


def test_a_known_field_still_builds_its_action():
    dfh.set_failure_context(screen="Attestation", page=FakePage())
    action = asyncio.run(dfh.generate_action(None, "AttestCheck", "click", POM["AttestCheck"], POM))
    assert action["type"] == "click" and action["locator"] == "#b"


def test_required_false_in_a_page_file_is_no_longer_forced_to_true():
    field = {"locator": "#opt", "type": "text", "required": False}
    action = asyncio.run(dfh.generate_action(None, "Opt", "x", field, {"Opt": field}))
    assert action["required"] is False
    plain_field = {"locator": "#o", "type": "text"}
    plain = asyncio.run(dfh.generate_action(None, "Opt2", "x", plain_field, {"Opt2": plain_field}))
    assert plain["required"] is True  # the default is unchanged


def test_duplicate_message_falls_back_to_full_paths_when_a_path_cannot_be_made_relative(monkeypatch):
    def other_drive(path, start):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(pc.os.path, "relpath", other_drive)
    message = pc.duplicate_message("Same", ["C:/a/Same.json", "C:/b/Same.json"], "D:/workspace")
    assert "C:/a/Same.json, C:/b/Same.json" in message
