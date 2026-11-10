"""AutoPlay Lite: the build script's output is complete, leaves out the other sections, and is up to date."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_build():
    spec = importlib.util.spec_from_file_location("build_lite", REPO / "tools" / "build_lite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def lite(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("lite") / "AutomationLite"
    _load_build().build(out)
    return out


def test_the_build_leaves_out_the_other_sections_and_the_apis(lite: Path):
    build = _load_build()
    for rel in build.EXCLUDE:
        assert not (lite / rel).exists(), rel
    for rel in ("webapp/ui/control.py", "webapp/ui/history.py", "webapp/ui/generator.py", "webapp/state.py",
                "tests/dynamic_test_suite.py", "conftest.py", "start.cmd", "setup.ps1", "setup.cmd", "README.md",
                "workspace/Data/systemData.json"):
        assert (lite / rel).exists(), rel
    setup = (lite / "setup.cmd").read_bytes()
    assert b"-ExecutionPolicy Bypass -File" in setup and b"setup.ps1" in setup and b"\r\n" in setup
    assert "setup.cmd" in (lite / "README.md").read_text(encoding="utf-8")
    assert "LITE = True" in (lite / "webapp" / "edition.py").read_text(encoding="utf-8")


def test_every_module_of_the_built_tree_imports_without_the_left_out_files(lite: Path):
    code = ("import importlib, pkgutil, webapp\n"
            "bad = []\n"
            "for m in pkgutil.walk_packages(webapp.__path__, 'webapp.'):\n"
            "    if m.name.endswith('__main__'):\n"
            "        continue\n"
            "    try:\n"
            "        importlib.import_module(m.name)\n"
            "    except Exception as e:\n"
            "        bad.append((m.name, repr(e)))\n"
            "assert not bad, bad\n"
            "from webapp.ui import shell\n"
            "from webapp.services.commands import TABS\n"
            "assert [t[0] for t in TABS] == ['Control', 'Generator', 'History'], TABS\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=lite,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_the_workspace_of_the_folder_is_kept_by_a_rebuild(tmp_path: Path):
    build = _load_build()
    out = tmp_path / "AutomationLite"
    build.build(out)
    mine = out / "workspace" / "Tests" / "TEAM" / "mine.json"
    mine.parent.mkdir(parents=True)
    mine.write_text("{}", encoding="utf-8")
    build.build(out)
    assert mine.exists()
    build.build(out, reset_workspace=True)
    assert not mine.exists()


def test_the_committed_folder_is_up_to_date(lite: Path):
    committed = REPO / "AutomationLite"
    if not committed.exists():
        pytest.skip("AutomationLite has not been built yet")
    diff = _load_build().differences(lite, committed)
    assert not diff, "AutomationLite is out of date; run python tools/build_lite.py\n" + "\n".join(diff[:20])


def test_the_full_edition_still_has_every_section():
    code = ("from webapp.services.commands import TABS\n"
            "assert len(TABS) == 12 and 'Schedules' in [t[0] for t in TABS]\n"
            "from webapp import edition\n"
            "assert not edition.LITE\n")
    env = {k: v for k, v in __import__("os").environ.items() if k != "AUTOPLAY_EDITION"}
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
