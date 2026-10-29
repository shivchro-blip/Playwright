"""autoGen code policy: what rand_gen_rules.json snippets may and may not do."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils import code_policy
from utils.rand_gen import RandGen

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("code", [
    "import random\ndef f():\n    return random.randint(1, 3)",
    "from datetime import date, timedelta\nimport string",
    "from faker import Faker\nfaker = Faker()",
    "import os\ndef d():\n    return os.path.join(cwd, 'Data')",
    "from os import path",
])
def test_data_only_snippets_pass(code):
    code_policy.check(code)


@pytest.mark.parametrize("code, reason", [
    ("import subprocess", "subprocess"),
    ("import os\nos.system('id')", "os.system"),
    ("from os import system", "only os.path"),
    ("open('/etc/passwd').read()", "open"),
    ("eval('1')", "eval"),
    ("__import__('socket')", "__import__"),
    ("().__class__.__bases__[0].__subclasses__()", "attribute .__"),
    ("getattr(object, 'x')", "getattr"),
    ("from . import x", "import from"),
    ("import urllib.request", "urllib.request"),
])
def test_risky_snippets_are_refused(code, reason):
    with pytest.raises(code_policy.CodePolicyError, match=reason.replace("(", r"\(")):
        code_policy.check(code)


def test_restricted_builtins_block_dynamic_imports():
    ns = code_policy.namespace(cwd=".")
    exec("import random\nx = random.random()", ns)
    with pytest.raises(ImportError):
        exec("import socket", ns)
    with pytest.raises(NameError):
        exec("open('x')", ns)


def test_shipped_rules_load_and_blocked_ones_are_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("RANDGEN_ALLOW_UNSAFE_CODE", raising=False)
    shipped = RandGen(str(REPO / "workspace" / "Data" / "rand_gen_rules.json"), str(tmp_path))
    assert shipped.broken == {} and shipped.generate_field_value("phone")
    rules = {"rand_gen_rules": {"leak": "leak()"},
             "custom_functions": {"leak": "import os\ndef leak():\n    return os.environ.get('HOME')"}}
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    assert "os.environ is not allowed" in RandGen(str(path), str(tmp_path)).broken["leak"]
    monkeypatch.setenv("RANDGEN_ALLOW_UNSAFE_CODE", "true")
    assert RandGen(str(path), str(tmp_path)).broken == {}
