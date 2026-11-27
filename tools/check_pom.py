"""Check a system's page objects (POMs) against its test cases.

The runner finds a screen by file name only: every .json under Pages/<SYSTEM> is stored as
screen_config_cache[<file name without .json>] and the LAST file found wins. Two POMs with the same file name in
different folders therefore shadow each other, and which one wins depends on the order the OS lists the folders.

Checks (exit code 1 if any ERROR):
  ERROR  duplicate screen name (same file name in two folders)
  ERROR  a POM or test file is not valid JSON
  ERROR  a test step uses a screen that has no POM
  ERROR  a test step fills a field that the screen's POM does not define
  ERROR  test case metadata: testCaseID must equal the file name, tags must be ["NYBE", <code>, "smoke", <testCaseID>]
  ERROR  description empty or cut off (ends with "and", "complete", "...", ",")
  INFO   POMs that no test uses

Usage:
  python check_pom.py <Pages/NYBE folder> <MasterRegressionSuite folder> [--order alpha|reverse]
--order picks which duplicate "wins" when simulating the loader, so a duplicate shows the failure it would cause.
"""
import json
import os
import sys
from collections import defaultdict

SKIP_PREFIXES = ("CUSTOMFIELD_",)


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def scan_poms(pages_dir, order):
    found = defaultdict(list)
    errors = []
    for dirpath, dirnames, filenames in os.walk(pages_dir):
        dirnames.sort(reverse=(order == "reverse"))
        for fn in sorted(filenames, reverse=(order == "reverse")):
            if fn.endswith(".json"):
                found[fn[:-5]].append(os.path.join(dirpath, fn))
    poms = {}
    for name, paths in found.items():
        if len(paths) > 1:
            rel = [os.path.relpath(p, pages_dir) for p in paths]
            errors.append(f"duplicate screen name '{name}': {', '.join(rel)} (the last one wins)")
        try:
            poms[name] = set(load(paths[-1]).get("fields", {}))
        except (OSError, ValueError) as e:
            errors.append(f"invalid POM {os.path.relpath(paths[-1], pages_dir)}: {e}")
    return poms, errors


def steps_with_screen(node):
    if isinstance(node, dict):
        if isinstance(node.get("screen"), str):
            yield node
        for v in node.values():
            yield from steps_with_screen(v)
    elif isinstance(node, list):
        for v in node:
            yield from steps_with_screen(v)


def step_fields(step):
    keys = list((step.get("formData") or {}).keys())
    keys += [d.get("field") for d in step.get("do", []) if isinstance(d, dict) and d.get("field")]
    for k in keys:
        base = k.split("|")[0]
        if not base.startswith(SKIP_PREFIXES):
            yield base


def check_metadata(fn, doc, errors):
    stem = fn[:-5]
    for tc in doc.get("testCases", []):
        tid = tc.get("testCaseID")
        tags = tc.get("tags") or []
        if tid != stem:
            errors.append(f"{fn}: testCaseID '{tid}' does not match the file name")
        if len(tags) != 4 or tags[0] != "NYBE" or tags[2] != "smoke" or tags[3] != tid:
            errors.append(f"{fn}: tags {tags} should be [\"NYBE\", <code>, \"smoke\", \"{tid}\"]")
        desc = (tc.get("description") or "").strip()
        if not desc or desc.endswith(("and", "complete", "...", ",", ":")):
            errors.append(f"{fn}: description is empty or cut off: '{desc[-40:]}'")


def main(argv):
    order = "alpha"
    if "--order" in argv:
        i = argv.index("--order")
        order = argv[i + 1]
        del argv[i:i + 2]
    pages_dir, tests_dir = argv[1], argv[2]
    poms, errors = scan_poms(pages_dir, order)
    used = set()
    for dirpath, _, filenames in os.walk(tests_dir):
        for fn in sorted(filenames):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                doc = load(path)
            except (OSError, ValueError) as e:
                errors.append(f"invalid test file {fn}: {e}")
                continue
            check_metadata(fn, doc, errors)
            for step in steps_with_screen(doc):
                screen = step["screen"]
                used.add(screen)
                if screen not in poms:
                    errors.append(f"{fn}: screen '{screen}' has no POM")
                    continue
                for field in step_fields(step):
                    if field not in poms[screen]:
                        errors.append(f"{fn}: screen '{screen}' has no field '{field}'")
    for e in sorted(set(errors)):
        print("ERROR ", e)
    unused = sorted(set(poms) - used)
    if unused:
        print("INFO   POMs no test uses:", ", ".join(unused))
    print(f"{len(poms)} screens, {len(set(errors))} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
