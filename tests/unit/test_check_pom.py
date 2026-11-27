"""tools/check_pom.py: duplicate screen names, missing screens/fields and test-case metadata."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_pom", ROOT / "tools" / "check_pom.py")
check_pom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_pom)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def case(name, steps, desc="Sign in and start the interview."):
    return {"testCases": [{"testCaseID": name, "tags": ["NYBE", "AAA", "smoke", name], "description": desc,
                           "steps": steps}]}


def run(pages, tests, capsys, order="alpha"):
    code = check_pom.main(["check_pom.py", str(pages), str(tests), "--order", order])
    return code, capsys.readouterr().out


def test_clean_tree_passes(tmp_path, capsys):
    write(tmp_path / "pages" / "A" / "Home.json", {"fields": {"go": {"locator": "#go", "type": "click"}}})
    write(tmp_path / "tests" / "AAA" / "TC_NYBE_AAA_001.json",
          case("TC_NYBE_AAA_001", [{"screen": "Home", "formData": {"go": "click", "CUSTOMFIELD_WAIT|2": "1"}}]))
    code, out = run(tmp_path / "pages", tmp_path / "tests", capsys)
    assert code == 0 and "0 error(s)" in out


def test_duplicate_screen_name_is_reported_for_either_folder_order(tmp_path, capsys):
    write(tmp_path / "pages" / "A" / "Attestation.json", {"fields": {"one": {"locator": "#1"}}})
    write(tmp_path / "pages" / "B" / "Attestation.json", {"fields": {"two": {"locator": "#2"}}})
    write(tmp_path / "tests" / "AAA" / "TC_NYBE_AAA_001.json",
          case("TC_NYBE_AAA_001", [{"screen": "Attestation", "formData": {"one": "click"}}]))
    for order in ("alpha", "reverse"):
        code, out = run(tmp_path / "pages", tmp_path / "tests", capsys, order)
        assert code == 1 and "duplicate screen name 'Attestation'" in out


def test_missing_screen_and_field(tmp_path, capsys):
    write(tmp_path / "pages" / "A" / "Home.json", {"fields": {"go": {"locator": "#go"}}})
    write(tmp_path / "tests" / "AAA" / "TC_NYBE_AAA_001.json",
          case("TC_NYBE_AAA_001", [{"screen": "Home", "formData": {"nope": "click"}},
                                   {"screen": "Ghost", "formData": {}}]))
    code, out = run(tmp_path / "pages", tmp_path / "tests", capsys)
    assert code == 1
    assert "no field 'nope'" in out and "screen 'Ghost' has no POM" in out


def test_metadata_rules(tmp_path, capsys):
    write(tmp_path / "pages" / "A" / "Home.json", {"fields": {}})
    bad = case("TC-NYBE-AAA-001", [], desc="Start the interview and")
    bad["testCases"][0]["tags"] = ["NYBE", "AAA", "smoke"]
    write(tmp_path / "tests" / "AAA" / "TC_NYBE_AAA_001.json", bad)
    code, out = run(tmp_path / "pages", tmp_path / "tests", capsys)
    assert code == 1
    assert "does not match the file name" in out and "tags" in out and "cut off" in out


def test_repo_nybe_workspace_is_consistent(capsys):
    pages = ROOT / "workspace" / "Pages" / "NYBE"
    tests = ROOT / "workspace" / "Tests" / "NYBE" / "MasterRegressionSuite"
    if not (pages.is_dir() and tests.is_dir()):
        return
    for order in ("alpha", "reverse"):
        code, out = run(pages, tests, capsys, order)
        assert code == 0, out
