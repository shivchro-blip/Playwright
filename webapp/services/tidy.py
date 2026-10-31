"""Tidy a page object: find what can be merged, removed or simplified, and build the plan that does it."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from webapp.services.pom_service import PomService
from webapp.services.refactor import CLICK_TYPES, Plan, Refactor
from webapp.services.test_case_service import TestCaseService
from webapp.services.usage import UsageIndex


@dataclass
class Finding:
    id: str
    kind: str  # autogen | duplicate | unused | twins
    title: str
    detail: str
    names: list[str] = field(default_factory=list)
    fixable: bool = True
    default: bool = False  # preselected in the dialog (only changes that cannot alter a run)
    canonical: str = ""  # duplicate groups: the field the others are merged into


def _fields(n: int) -> str:
    return f"{n} field{'' if n == 1 else 's'}"


def _same(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, ensure_ascii=False)


def analyze(pom: PomService, tests: TestCaseService, system: str, page: str) -> list[Finding]:
    configs = pom.field_configs(system, page)
    usage = UsageIndex(tests).scan(system)
    findings: list[Finding] = []

    redundant = [n for n, c in configs.items() if c.get("type") in CLICK_TYPES
                 and str(c.get("autoGen", "")).strip().lower() == "click"]
    if redundant:
        findings.append(Finding(
            "autogen", "autogen",
            f"{_fields(len(redundant))} {'has' if len(redundant) == 1 else 'have'} a redundant autoGen \"click\"",
            "Buttons, links, checkboxes and radios click by default, so autoGen \"click\" changes nothing.",
            redundant, True, True))

    groups: dict[str, list[str]] = defaultdict(list)
    for name, cfg in configs.items():
        groups[_same(cfg)].append(name)
    for n, names in enumerate(g for g in groups.values() if len(g) > 1):
        findings.append(Finding(
            f"duplicate-{n}", "duplicate", f"{len(names)} fields are exactly the same: {', '.join(names)}",
            f"Merge into '{names[0]}'. A step that uses several of them gets '{names[0]}', '{names[0]}|', "
            f"'{names[0]}||' (the engine's way to use a field again).", names, True, False, names[0]))

    unused = [n for n in configs if not usage.field_uses(page, n)]
    if unused:
        findings.append(Finding(
            "unused", "unused",
            f"{_fields(len(unused))} {'is' if len(unused) == 1 else 'are'} not used by any test or common action",
            "Removing them cannot change a run. Fields used only by tests in another system's folder that point "
            "at this page are counted, but check before removing if you keep tests elsewhere.", unused, True, False))

    twin_groups: dict[str, list[str]] = defaultdict(list)
    for name, cfg in configs.items():
        twin_groups[_same({k: v for k, v in cfg.items() if k != "index"})].append(name)
    for n, names in enumerate(g for g in twin_groups.values() if len(g) > 1):
        indexes = {str(configs[x].get("index", "")) for x in names}
        if len(indexes) == len(names):
            findings.append(Finding(
                f"twins-{n}", "twins", f"{', '.join(names)} differ only by index ({', '.join(sorted(indexes))})",
                "Mark one of them \"indexed\": true and run the step again with a step \"index\" instead of "
                "keeping a copy of every field for each repeated section.", names, False))
    return findings


def build_plan(pom: PomService, tests: TestCaseService, system: str, page: str, findings: list[Finding]) -> Plan:
    """The combined changes of the chosen findings (merges first, so removals see the merged steps)."""
    refactor = Refactor(pom, tests)
    chosen = [f for f in findings if f.fixable]
    for finding in chosen:
        if finding.kind == "autogen":
            refactor.drop_autogen_click(system, page, finding.names)
    merged: set[str] = set()
    for finding in chosen:
        if finding.kind == "duplicate":
            refactor.merge_fields(system, page, finding.canonical, [n for n in finding.names if n != finding.canonical])
            merged.update(finding.names[1:])
    for finding in chosen:
        if finding.kind == "unused":
            names = [n for n in finding.names if n not in merged]
            if names:
                refactor.remove_fields(system, page, names)
    return refactor.plan()
