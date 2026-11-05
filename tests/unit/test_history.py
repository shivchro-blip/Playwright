"""Edit history: versions kept on every save, listed newest first, diffed and restored."""
from __future__ import annotations

import json
import time

import pytest

from webapp.services import history
from webapp.services.json_files import ConflictError, read_json, write_json


@pytest.fixture
def ws(tmp_path):
    for folder in ("Data", "Pages/S", "Tests/S/Suite"):
        (tmp_path / folder).mkdir(parents=True)
    return tmp_path


def save(path, data, expected=None):
    return write_json(path, data, expected if expected is not None else (read_json(path)[1] if path.exists() else None))


def test_each_save_keeps_the_version_it_replaces(ws):
    page = ws / "Pages" / "S" / "Home.json"
    save(page, {"fields": {"a": 1}})
    assert history.versions(page) == []  # a new file has no past
    save(page, {"fields": {"a": 2}})
    time.sleep(0.01)
    save(page, {"fields": {"a": 3}})
    found = history.versions(page)
    assert len(found) == 2 and found[0].id > found[1].id  # newest first
    assert json.loads(history.read_version(page, found[0].id))["fields"]["a"] == 2
    assert json.loads(history.read_version(page, found[1].id))["fields"]["a"] == 1
    assert (ws / ".history" / "Pages" / "S" / "Home.json").is_dir()


def test_an_unchanged_resave_adds_no_version_and_old_ones_are_pruned(ws, monkeypatch):
    monkeypatch.setattr(history, "KEEP", 3)
    page = ws / "Data" / "TestData.json"
    for n in range(8):
        save(page, {"n": n})
        time.sleep(0.005)
    found = history.versions(page)
    assert len(found) == 3
    assert [json.loads(history.read_version(page, v.id))["n"] for v in found] == [6, 5, 4]
    save(page, {"n": 7})  # keeps the n=7 it replaces
    ids = [v.id for v in history.versions(page)]
    save(page, {"n": 7})  # the version it replaces equals the newest kept one: nothing new
    assert [v.id for v in history.versions(page)] == ids


def test_restoring_goes_through_a_normal_save_so_it_can_be_undone(ws):
    page = ws / "Tests" / "S" / "Suite" / "T.json"
    save(page, {"v": 1})
    save(page, {"v": 2})
    old = history.versions(page)[0]
    restored = json.loads(history.read_version(page, old.id))
    data, sha, _ = read_json(page)
    save(page, restored, sha)
    assert read_json(page)[0] == {"v": 1}
    assert [json.loads(history.read_version(page, v.id))["v"] for v in history.versions(page)] == [2, 1]


def test_a_stale_hash_still_refuses_and_keeps_no_version(ws):
    page = ws / "Pages" / "S" / "A.json"
    save(page, {"x": 1})
    with pytest.raises(ConflictError):
        write_json(page, {"x": 2}, "not-the-hash")
    assert history.versions(page) == []


def test_diff_shows_what_changed():
    lines = history.diff('{\n  "a": 1\n}\n', '{\n  "a": 2\n}\n')
    assert "-  \"a\": 1" in lines and "+  \"a\": 2" in lines and lines[0].startswith("--- version")


def test_only_valid_ids_and_files_inside_the_workspace_are_read(ws, tmp_path):
    page = ws / "Pages" / "S" / "A.json"
    save(page, {"x": 1})
    save(page, {"x": 2})
    for bad in ("../../../etc/passwd", "x.json", "", "20200101-000000-000.json"):
        with pytest.raises(ValueError):
            history.read_version(page, bad)
    outside = tmp_path.parent / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    assert history.versions(outside) == [] and history.folder_for(outside) is None


def test_a_folder_named_data_inside_tests_does_not_confuse_the_root(ws):
    nested = ws / "Tests" / "S" / "Suite" / "Data" / "x.json"
    nested.parent.mkdir(parents=True)
    save(nested, {"a": 1})
    save(nested, {"a": 2})
    assert history.workspace_root(nested) == ws.resolve()
    assert history.versions(nested)
