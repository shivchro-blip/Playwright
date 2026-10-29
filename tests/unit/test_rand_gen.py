"""RandGen (autoGen data) must fail loudly instead of silently producing no value."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from utils.rand_gen import RandGen, RandGenError

REPO_RULES = Path(__file__).resolve().parents[2] / "workspace" / "Data" / "rand_gen_rules.json"


def _gen(tmp_path: Path, config: dict) -> RandGen:
    path = tmp_path / "rand_gen_rules.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return RandGen(str(path), str(tmp_path))


def test_shipped_rules_still_generate_values():
    gen = RandGen(str(REPO_RULES), str(REPO_RULES.parents[1]))
    assert gen.broken == {} and gen.error is None
    assert re.fullmatch(r"\d{3}-\d{3}-\d{4}", gen.generate_field_value("phone"))
    assert 1 <= int(gen.generate_field_value("getIntBetween(1, 5)")) < 5
    assert gen.generate_field_value("getStrBetween('AB', 6, case='upper')").startswith("AB")
    assert gen.generate_field_value("amt_Rec") == "900"  # constant
    assert gen.generate_field_value("city")  # random_address.*


def test_unknown_rule_raises_with_its_name(tmp_path: Path):
    gen = _gen(tmp_path, {"rand_gen_rules": {"phone": "123"}})
    with pytest.raises(RandGenError, match="Unknown autoGen rule 'phnoe'"):
        gen.generate_field_value("phnoe")


def test_one_broken_function_does_not_block_the_others(tmp_path: Path):
    gen = _gen(tmp_path, {
        "rand_gen_rules": {"bad": "bad_fn()", "good": "good_fn()"},
        "custom_functions": {"bad_fn": "def bad_fn(:\n  pass", "good_fn": "def good_fn():\n  return 'ok'"},
    })
    assert gen.generate_field_value("good") == "ok"
    with pytest.raises(RandGenError, match=r"calls bad_fn\(\), which is not available: SyntaxError"):
        gen.generate_field_value("bad")


def test_function_error_is_reported(tmp_path: Path):
    gen = _gen(tmp_path, {"rand_gen_rules": {"boom": "boom()"},
                          "custom_functions": {"boom": "def boom():\n  raise RuntimeError('nope')"}})
    with pytest.raises(RandGenError, match=r"failed in boom\(\): RuntimeError: nope"):
        gen.generate_field_value("boom")


def test_missing_function_is_reported(tmp_path: Path):
    gen = _gen(tmp_path, {"rand_gen_rules": {"x": "not_defined()"}})
    with pytest.raises(RandGenError, match="not defined in custom_functions"):
        gen.generate_field_value("x")


def test_bare_name_that_is_not_a_function_stays_a_constant(tmp_path: Path):
    gen = _gen(tmp_path, {"rand_gen_rules": {"obj": "faker"},
                          "dynamic_objects": {"faker": "faker = object()"}})
    assert gen.generate_field_value("obj") == "faker"


def test_bad_config_file_raises_on_first_use_not_at_import(tmp_path: Path):
    path = tmp_path / "rand_gen_rules.json"
    path.write_text("{ not json", encoding="utf-8")
    gen = RandGen(str(path), str(tmp_path))  # constructing never raises (module import must not crash)
    with pytest.raises(RandGenError, match="Could not load autoGen rules"):
        gen.generate_field_value("phone")


def test_bad_expression_is_reported(tmp_path: Path):
    gen = _gen(tmp_path, {"rand_gen_rules": {"f": "f()"}})
    with pytest.raises(RandGenError, match="Invalid"):
        gen.generate_field_value("f(1, ")


def test_keyword_arguments_are_passed(tmp_path: Path):
    gen = RandGen(str(REPO_RULES), str(REPO_RULES.parents[1]))
    value = gen.generate_field_value("getStrBetween('AB', 8, case='upper')")
    assert value.startswith("AB") and len(value) == 8 and value[2:] == value[2:].upper()
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", gen.generate_field_value("getDateBetween(1, 5, date_format='%m/%d/%Y')"))


def test_arguments_are_never_executed(tmp_path: Path):
    gen = _gen(tmp_path, {"rand_gen_rules": {"f": "f()"}, "custom_functions": {"f": "def f(*a, **k):\n  return a"}})
    with pytest.raises(RandGenError, match="Invalid arguments"):
        gen.generate_field_value("f(__import__('os').getcwd())")
