"""Replace the legal-name generators in rand_gen_rules.json with short, clean ones.

Usage:  python fix_legal_names.py "C:\\NYBE\\playwrightweb\\workspace\\Data\\rand_gen_rules.json"
A backup is saved next to the file as rand_gen_rules.json.bak. Safe to run more than once.

New names look like  JENNIFER WARD LLC  /  MARK OBRIEN CORPORATION: one first name and one last name (letters
only, so no "DR.", "JR.", "MD", apostrophes or hyphens) followed by the legal indicator.
"""
import json
import shutil
import sys
from pathlib import Path

# function name -> (rule name, indicator typed after the name)
KINDS = {
    "generate_legalName":            ("legalName", "LLC"),
    "generate_legalNameLLC":         ("legalNameLLC", "LLC"),
    "generate_legalNameLLP":         ("legalNameLLP", "LLP"),
    "generate_legalNameCorporation": ("legalNameCorporation", "Corporation"),
    "generate_legalNameLimited":     ("legalNameLimited", "Limited"),
    "generate_legalNameLtd":         ("legalNameLtd", "Ltd."),
    "generate_legalNameIncorp":      ("legalNameIncorp", "Incorporated"),
    "generate_legalNameInc":         ("legalNameInc", "Inc."),
}


def build(fn, indicator):
    return (
        "\nimport re\n"
        f"def {fn}():\n"
        "   try:\n"
        "       first = re.sub(r'[^A-Za-z]', '', faker.first_name())\n"
        "       last = re.sub(r'[^A-Za-z]', '', faker.last_name())\n"
        f"       return (first + ' ' + last).upper() + ' {indicator}'\n"
        "   except Exception as e:\n"
        f"       print(f'{fn}() - Error in function: {{e}}')\n"
        "       return None"
    )


def patch_text(text):
    """Edit only the legal-name lines; everything else (your address list, spacing, comments) stays as it is."""
    lines = text.split("\n")
    done, notes = set(), []

    def find(prefix):
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith(prefix):
                return i
        return None

    for fn, (rule, indicator) in KINDS.items():
        i = find(f'"{fn}":')
        value = json.dumps(build(fn, indicator))
        if i is None:
            if fn == "generate_legalNameLLP":
                j = find('"generate_legalNameInc":')
                if j is not None:
                    indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
                    if not lines[j].rstrip().endswith(","):
                        lines[j] = lines[j].rstrip() + ","
                    lines.insert(j + 1, f'{indent}"{fn}": {value},')
                    done.add(fn)
                    continue
            notes.append(f"function {fn} not found: add it by hand")
            continue
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        comma = "," if lines[i].rstrip().endswith(",") else ""
        lines[i] = f'{indent}"{fn}": {value}{comma}'
        done.add(fn)
    if find('"legalNameLLP":') is None:
        j = find('"legalNameInc":')
        if j is None:
            notes.append('rule "legalNameLLP" not added: add  "legalNameLLP": "generate_legalNameLLP()"  by hand')
        else:
            indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
            if not lines[j].rstrip().endswith(","):
                lines[j] = lines[j].rstrip() + ","
            lines.insert(j + 1, f'{indent}"legalNameLLP": "generate_legalNameLLP()",')
    return "\n".join(lines), done, notes


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    new, done, notes = patch_text(text)
    json.loads(new)  # refuse to write a broken file
    shutil.copyfile(path, str(path) + ".bak")
    path.write_bytes(new.encode("utf-8"))
    print(f"Updated {len(done)} legal-name generators in {path} (backup: {path}.bak)")
    for n in notes:
        print("NOTE:", n)
