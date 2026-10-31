"""The CI template must keep matching the CLI it calls."""
import re
from pathlib import Path

from webapp.cli_run import _parser

TEMPLATE = Path(__file__).resolve().parents[2] / "docs" / "ci" / "github-actions-suite.yml"
HELP = Path(__file__).resolve().parents[2] / "docs" / "help" / "web" / "ci.md"


def test_template_calls_the_cli_with_options_it_has():
    text = TEMPLATE.read_text(encoding="utf-8")
    call = re.search(r"Run the tests\n\s*run: >\n(.*?)\n\s*- name", text, re.S).group(1)
    assert "python -m webapp.cli_run" in call
    flags = set(re.findall(r"--[a-z][a-z-]*", call))
    assert flags and flags <= set(_parser()._option_string_actions), flags
    assert "requirements-lock.txt" in text and "--require-hashes" in text


def test_help_documents_every_cli_option():
    text = HELP.read_text(encoding="utf-8")
    for flag in _parser()._option_string_actions:
        if flag.startswith("--") and flag != "--help":
            assert flag in text, f"{flag} is not in Help > Run in CI"
