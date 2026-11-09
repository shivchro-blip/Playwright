"""Plain-language steps: write what the test does in your own words, get explicit steps on real fields.

    Open the apply page
    Type "Ann" into first name
    Choose "Texas" from state
    Tick I agree
    Click Submit

Each line is read as an intent (do what, on which thing, with what value), and the thing is matched against the
fields of the system's pages (their names and what their locators say: label, placeholder, text, role name), so a
compiled step only ever names a field that exists. The page a line acts on is the page the previous line was on,
unless a line opens another one ("Open the apply page", "On Payment"); a field found only on another page moves the
test there. Lines the rules cannot read or match are left for AI assist, which is given only the real fields of the
pages and whose answer is checked the same way: a field it names that does not exist is dropped, never guessed.

The result is a list of explicit steps (utils/steps_v2.py) and, for each line, what it became and how sure the match
is, so a person reviews it before it goes into a test.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field

ACTIONS = ("fill", "click", "select", "check", "press", "open")
TYPE_FIT = {"fill": {"text", "input", "textarea", "number", "date", "datepicker"}, "click": {"button", "link"},
            "select": {"select", "radio"}, "check": {"checkbox", "radio"}, "press": {"keyboard", "text", "input"}}
MIN_SCORE = 0.5
MARGIN = 0.1
STOP = {"the", "a", "an", "field", "box", "button", "link", "input", "on", "in", "into", "to", "of", "for", "page",
        "screen", "tab", "option", "dropdown", "checkbox", "my", "your"}

_Q = r"[\"'“”‘’](?P<v>[^\"'“”‘’]*)[\"'“”‘’]"
PATTERNS = [
    ("open", re.compile(r"^(?:go to|open|navigate to|on|start on|start at)\s+(?P<page>.+?)$", re.I)),
    ("fill", re.compile(rf"^(?:type|enter|fill in|fill|write|put|input)\s+{_Q}\s+(?:in|into|as|for|on)\s+(?P<t>.+)$",
                        re.I)),
    ("fill", re.compile(rf"^(?:fill|fill in|set|enter)\s+(?P<t>.+?)\s+(?:with|to|as)\s+{_Q}$", re.I)),
    ("select", re.compile(rf"^(?:choose|select|pick)\s+{_Q}\s+(?:in|from|for|as)\s+(?P<t>.+)$", re.I)),
    ("select", re.compile(rf"^(?:set|choose|select)\s+(?P<t>.+?)\s+(?:to|as)\s+{_Q}$", re.I)),
    ("press", re.compile(r"^press\s+(?P<v>enter|tab|escape|esc|space|arrowdown|arrowup)(?:\s+(?:in|on)\s+(?P<t>.+))?$",
                         re.I)),
    ("check", re.compile(r"^(?:check|tick|accept|agree to|turn on)\s+(?P<t>.+)$", re.I)),
    ("click", re.compile(r"^(?:click|click on|press|tap|hit|submit|follow)\s+(?P<t>.+)$", re.I)),
]


@dataclass
class Intent:
    line: str
    action: str = ""
    target: str = ""
    value: str = ""
    page: str = ""  # for "open"


@dataclass
class Row:
    line: str
    status: str  # resolved | ambiguous | unresolved | page | skipped
    page: str = ""
    field: str = ""
    action: str = ""
    value: str = ""
    score: float = 0.0
    source: str = "rules"  # rules | ai
    candidates: list[str] = dc_field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Compiled:
    rows: list[Row]
    steps: list[dict]

    @property
    def ok(self) -> bool:
        return all(r.status in ("resolved", "page", "skipped") for r in self.rows)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "rows": [r.to_dict() for r in self.rows], "steps": self.steps}


def words(text: str) -> list[str]:
    """Lower-case words of a name or a phrase: camelCase, snake_case and punctuation split."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def field_words(name: str, cfg: dict) -> set[str]:
    out = set(words(name))
    for key in ("label", "placeholder", "text", "name", "contains"):
        if isinstance(cfg.get(key), str):
            out |= set(words(cfg[key]))
    locator = cfg.get("locator")
    if isinstance(locator, str):  # ids and names inside a CSS locator say what the field is
        for m in re.findall(r"(?:#|name=['\"]?|id=['\"]?|aria-label=['\"]?|placeholder=['\"]?)([\w\- ]+)", locator):
            out |= set(words(m))
    identity = cfg.get("identity") or {}
    fp = identity.get("fingerprint") if isinstance(identity, dict) else None
    if isinstance(fp, dict):
        for key in ("label", "aria", "placeholder", "text"):
            if isinstance(fp.get(key), str):
                out |= set(words(fp[key]))
    return out


def parse(line: str) -> Intent:
    text = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line).strip().rstrip(".")
    for action, pattern in PATTERNS:
        m = pattern.match(text)
        if m:
            g = m.groupdict()
            return Intent(line, action, (g.get("t") or "").strip(), (g.get("v") or "").strip(),
                          (g.get("page") or "").strip())
    return Intent(line)


def _score(target: str, fwords: set[str], ftype: str, action: str) -> float:
    t = set(words(target))
    if not t or not fwords:
        return 0.0
    overlap = len(t & fwords)
    if not overlap:
        return 0.0
    score = overlap / len(t | fwords) * 0.6 + overlap / len(t) * 0.4
    if ftype in TYPE_FIT.get(action, set()):
        score += 0.15
    return round(min(score, 1.0), 2)


def match_page(phrase: str, pages: dict[str, dict[str, dict]]) -> str:
    want = set(words(phrase))
    best, top = "", 0.0
    for name in pages:
        have = set(words(name))
        if not want or not have:
            continue
        s = len(want & have) / len(want | have)
        if s > top:
            best, top = name, s
    return best if top >= 0.5 else ""


def compile_lines(text: str, pages: dict[str, dict[str, dict]], start_page: str = "",
                  resolve_rest=None) -> Compiled:
    """`pages`: page -> {field: config} of the system. `resolve_rest(lines, pages)`: optional (AI) resolver for lines
    the rules could not read or match; returns {line: {"page", "field", "action", "value"}}."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    vocab = {p: {f: field_words(f, c) for f, c in fs.items()} for p, fs in pages.items()}
    rows: list[Row] = []
    page = start_page if start_page in pages else ""
    for line in lines:
        intent = parse(line)
        if intent.action == "open":
            found = match_page(intent.page, pages)
            if found:
                page = found
                rows.append(Row(line, "page", page=found, action="open"))
            else:
                rows.append(Row(line, "unresolved", action="open", note=f"No page like {intent.page!r}."))
            continue
        if not intent.action:
            rows.append(Row(line, "unresolved", note="Not a step the rules can read."))
            continue
        if intent.action == "press" and not intent.target:  # "Press Enter": in the field just used
            last = next((r for r in reversed(rows) if r.status == "resolved"), None)
            if last is None:
                rows.append(Row(line, "unresolved", action="press", value=intent.value,
                                note="Press a key in which field? Say it: Press Enter in search."))
            else:
                rows.append(Row(line, "resolved", page=last.page, field=last.field, action="press",
                                value=intent.value, score=last.score))
            continue
        scored = []
        for p, fields in vocab.items():
            for f, fw in fields.items():
                s = _score(intent.target or intent.value, fw, str(pages[p][f].get("type") or ""), intent.action)
                if s:
                    scored.append((s + (0.1 if p == page else 0), p, f))
        scored.sort(key=lambda x: (-x[0], x[1] != page, x[1], x[2]))
        value = intent.value if intent.action in ("fill", "select", "press") else ""
        if not scored or scored[0][0] < MIN_SCORE:
            rows.append(Row(line, "unresolved", action=intent.action, value=value,
                            candidates=[f"{p}.{f}" for _, p, f in scored[:3]],
                            note=f"No field like {intent.target!r}."))
            continue
        best = scored[0]
        if len(scored) > 1 and best[0] - scored[1][0] < MARGIN and (scored[1][1], scored[1][2]) != best[1:]:
            rows.append(Row(line, "ambiguous", action=intent.action, value=value, score=best[0],
                            candidates=[f"{p}.{f}" for _, p, f in scored[:3]],
                            note="Several fields fit: name it more exactly."))
            continue
        page = best[1]
        rows.append(Row(line, "resolved", page=best[1], field=best[2], action=intent.action, value=value,
                        score=min(best[0], 1.0)))
    if resolve_rest is not None:
        open_rows = [r for r in rows if r.status in ("unresolved", "ambiguous") and r.action != "open"]
        if open_rows:
            answers = resolve_rest([r.line for r in open_rows], pages) or {}
            for r in open_rows:
                a = answers.get(r.line)
                if not a:
                    continue
                p, f, act = str(a.get("page") or ""), str(a.get("field") or ""), str(a.get("action") or "")
                if p in pages and f in pages[p] and act in ACTIONS and act != "open":
                    r.status, r.page, r.field, r.action, r.source = "resolved", p, f, act, "ai"
                    r.value = str(a.get("value") or "") if act in ("fill", "select", "press") else ""
                    r.note, r.candidates, r.score = "", [], 0.0
                else:
                    r.note = (r.note + " " if r.note else "") + "AI named a field that does not exist: ignored."
    return Compiled(rows, to_steps(rows))


def to_steps(rows: list[Row]) -> list[dict]:
    """Explicit steps (steps v2) from the resolved rows, one step per run of lines on the same page."""
    steps: list[dict] = []
    for r in rows:
        if r.status != "resolved":
            continue
        item = {"field": r.field}
        if r.action in ("fill", "select"):
            item["value"] = r.value
        elif r.action == "press":
            item["press"] = {"esc": "Escape"}.get(r.value.lower(), r.value.capitalize() if r.value else "Enter")
        else:
            item[r.action] = True
        if steps and steps[-1]["screen"] == r.page:
            steps[-1]["do"].append(item)
        else:
            steps.append({"screen": r.page, "do": [item]})
    return steps
