"""Hide sensitive values (SSNs, card numbers, anything you describe) in run logs, messages and failure details.

Test data looks real, and logs and reports get shared. Masking is applied to console output as it arrives, to test
messages, and to the failure details shown in the app. Screenshots and raw files in the run folder are NOT changed.

Configure it in <workspace>/Data/maskPatterns.json (optional):

    { "builtin": ["ssn", "card"],                      # also available: "routing"
      "custom": [ { "name": "policy", "pattern": "POL-\\\\d{6}", "replacement": "POL-******" } ] }

Without the file, "ssn" and "card" are on. "builtin": [] turns the built-in rules off.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
# 13-19 digits, contiguous or in groups of 4 (Amex 4-6-5), starting like a real card number
CARD = re.compile(r"(?<![\d-])(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|3[47]\d{2}([ -])\d{6}\2\d{5})(?![\d-])")
CARD_PREFIX = re.compile(r"^(4|5[1-5]|2(2[2-9]\d|[3-6]\d\d|7[01]\d|720)|3[47]|6011|65|35(2[89]|[3-8]\d))")
ROUTING = re.compile(r"(?<![\d-])\d{9}(?![\d-])")
ROUTING_PREFIX = re.compile(r"^(0[1-9]|1[0-2]|2[1-9]|3[0-2]|6[1-9]|7[0-2]|80)")
DEFAULT_BUILTIN = ("ssn", "card")
BUILTIN_NAMES = ("ssn", "card", "routing")


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    if 13 <= len(digits) <= 19 and CARD_PREFIX.match(digits) and luhn(digits):
        return f"[card ****{digits[-4:]}]"
    return m.group(0)


def _routing(m: re.Match) -> str:
    d = [int(c) for c in m.group(0)]
    checksum = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
    return "[routing *****" + m.group(0)[-4:] + "]" if ROUTING_PREFIX.match(m.group(0)) and checksum % 10 == 0 \
        else m.group(0)


class Masker:
    def __init__(self, config_path: Path | None = None):
        self.path = Path(config_path) if config_path else None
        self._stamp: tuple | None = None
        self._rules: list[Callable[[str], str]] = []
        self._load()

    # ───── configuration ─────

    def _load(self) -> None:
        stamp = None
        try:
            if self.path and self.path.is_file():
                stat = self.path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            pass
        if stamp == self._stamp and self._rules:
            return
        self._stamp = stamp
        builtin, custom = list(DEFAULT_BUILTIN), []
        if stamp is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                builtin, custom = self._parse(data)
            except (OSError, ValueError) as e:
                logger.warning("Ignoring %s: %s (using the built-in masking)", self.path, e)
        rules: list[Callable[[str], str]] = []
        if "ssn" in builtin:
            rules.append(lambda t: SSN.sub("***-**-****", t))
        if "card" in builtin:
            rules.append(lambda t: CARD.sub(_card, t))
        if "routing" in builtin:
            rules.append(lambda t: ROUTING.sub(_routing, t))
        for name, pattern, replacement in custom:
            rules.append(lambda t, p=pattern, r=replacement or f"[masked {name}]": p.sub(r, t))
        self._rules = rules or [lambda t: t]

    @staticmethod
    def _parse(data) -> tuple[list[str], list[tuple[str, re.Pattern, str]]]:
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        builtin = data.get("builtin", list(DEFAULT_BUILTIN))
        if not isinstance(builtin, list) or any(b not in BUILTIN_NAMES for b in builtin):
            raise ValueError(f'"builtin" may list: {", ".join(BUILTIN_NAMES)}')
        custom = []
        for i, item in enumerate(data.get("custom") or [], start=1):
            if not isinstance(item, dict) or not item.get("pattern"):
                raise ValueError(f'custom rule {i} needs a "pattern"')
            try:
                custom.append((str(item.get("name") or f"rule{i}"), re.compile(str(item["pattern"])),
                               str(item.get("replacement") or "")))
            except re.error as e:
                raise ValueError(f'custom rule {i}: {e}') from e
        return builtin, custom

    # ───── use ─────

    def mask(self, text: str) -> str:
        if not text:
            return text
        self._load()
        for rule in self._rules:
            text = rule(text)
        return text
