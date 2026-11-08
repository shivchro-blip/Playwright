"""The command palette's commands, and how a typed query ranks them (webapp/ui/palette.py shows them).

One box finds everything: the app's sections, its actions, every test case, page, field and journey in the
workspace (from the workspace model, so it stays current), and the latest runs. Typing ranks by how well the words
match: the whole title, its start, the start of a word in it, anywhere in it, then the letters in order ("tcsm"
finds TC-SMOKE). Every word typed has to match somewhere (the title, the details line or the extra keywords).

A command carries what to do as data (`action`, `args`); the page decides how to do it, so this module has no UI
and is tested on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TABS = [
    ("Control", "circle-play", "run start tests console"),
    ("Confidence", "chart-column", "safe to release ready blocked risk working production verdict"),
    ("Journeys", "circle-check", "business coverage status"),
    ("Releases", "shield", "go no-go evidence release check"),
    ("Monitors", "activity", "production uptime incidents"),
    ("Impact", "git-compare", "change diff affected tests"),
    ("Generator", "list-plus", "editor test cases pages data sets common actions record"),
    ("History", "history", "runs results reports flaky heals"),
    ("Schedules", "calendar-clock", "cron nightly timer"),
    ("Automations", "bell", "when then rule trigger deploy webhook notify after"),
    ("Help", "circle-help", "docs guide documentation"),
]
TAB_ICONS = {name: ico for name, ico, _ in TABS}
KIND_ORDER = {"tab": 0, "action": 1, "test": 2, "journey": 3, "page": 4, "field": 5, "run": 6}
RECENT_RUNS = 8
EMPTY_LIMIT = 14


@dataclass
class Command:
    id: str
    kind: str  # tab | action | test | journey | page | field | run
    title: str
    subtitle: str = ""
    icon: str = ""
    action: str = ""  # what the page does: tab | open_test | open_page | open_journey | open_run | open_trends
    args: dict = field(default_factory=dict)
    keywords: str = ""


def tab_commands() -> list[Command]:
    return [Command(f"tab:{name}", "tab", name, "Go to section", ico, "tab", {"name": name}, words)
            for name, ico, words in TABS]


def action_commands() -> list[Command]:
    return [
        Command("action:run", "action", "Start a run", "Control: choose a system, environment and tags",
                "circle-play", "tab", {"name": "Control"}, "new run execute"),
        Command("action:new-test", "action", "New test case", "Generator: test cases", "plus",
                "tab", {"name": "Generator", "section": "tests"}, "create add"),
        Command("action:trends", "action", "Open trends", "Pass rate, slowest tests and where tests fail",
                "chart-column", "open_trends", {}, "charts statistics history"),
        Command("action:help", "action", "Search the help", "Help", "circle-help", "tab", {"name": "Help"},
                "docs how"),
    ]


def model_commands(snapshot) -> list[Command]:
    """Test cases, pages, fields and journeys of the workspace (a workspace_model.Snapshot)."""
    out: list[Command] = []
    for (system, test_id), node in sorted(snapshot.tests.items()):
        tags = ", ".join(node.tags)
        out.append(Command(f"test:{system}:{test_id}", "test", test_id,
                           f"Test case · {system} · {node.rel}" + (f" · {tags}" if tags else ""), "list-checks",
                           "open_test", {"system": node.folder, "rel": node.rel, "index": node.index},
                           tags))
    for (system, page), fields in sorted(snapshot.pages.items()):
        out.append(Command(f"page:{system}:{page}", "page", page, f"Page · {system}", "app-window", "open_page",
                           {"system": system, "page": page}))
        for name in sorted(fields):
            out.append(Command(f"field:{system}:{page}:{name}", "field", name, f"Field of {page} · {system}",
                               "tag", "open_page", {"system": system, "page": page, "field": name}, page))
    for journey in snapshot.journeys:
        name, system = str(journey.get("name", "")), str(journey.get("system", ""))
        if name:
            out.append(Command(f"journey:{system}:{journey.get('id', name)}", "journey", name,
                               f"Journey · {system}", "circle-check", "open_journey",
                               {"system": system, "id": str(journey.get("id", ""))}))
    return out


def run_commands(runs, limit: int = RECENT_RUNS) -> list[Command]:
    out = []
    for run in list(runs)[:limit]:
        when = (run.created_at or "").replace("T", " ")[:16]
        out.append(Command(f"run:{run.id}", "run", run.id,
                           f"Run · {run.config.system} / {run.config.env} · {run.status} · {when}", "history",
                           "open_run", {"id": run.id},
                           " ".join([run.status, run.config.system, run.config.env, *run.config.tags])))
    return out


def build(snapshot=None, runs=()) -> list[Command]:
    return [*tab_commands(), *action_commands(), *(model_commands(snapshot) if snapshot is not None else []),
            *run_commands(runs)]


# ───── ranking ─────

def _subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def _word_starts(hay: str) -> list[int]:
    return [i for i, ch in enumerate(hay) if ch.isalnum() and (i == 0 or not hay[i - 1].isalnum()
                                                                or (hay[i - 1].islower() and ch.isupper()))]


def score(query: str, command: Command) -> int:
    """How well the query matches (0 = not at all). Higher is better."""
    words = query.casefold().split()
    if not words:
        return 1
    raw_title = command.title
    title = raw_title.casefold()
    rest = f"{command.subtitle} {command.keywords}".casefold()
    starts = {i for i in _word_starts(raw_title)}
    total = 0
    whole = query.strip().casefold()
    if title == whole:
        total += 1000
    elif title.startswith(whole):
        total += 600
    for word in words:
        at = title.find(word)
        if at == 0:
            total += 300
        elif at > 0 and (at in starts or not title[at - 1].isalnum()):
            total += 220
        elif at > 0:
            total += 150
        elif _subsequence(word, title):
            total += 60
        elif word in rest:
            total += 40
        else:
            return 0
    return total - min(len(title), 60) // 4


def rank(query: str, commands: list[Command], limit: int = 20) -> list[Command]:
    """The best matches, best first. An empty query lists the sections and the latest runs."""
    if not query.strip():
        picked = [c for c in commands if c.kind in ("tab", "run")]
        return picked[:EMPTY_LIMIT]
    scored = [(score(query, c), c) for c in commands]
    scored = [(s, c) for s, c in scored if s > 0]
    scored.sort(key=lambda sc: (-sc[0], KIND_ORDER.get(sc[1].kind, 9), sc[1].title.casefold()))
    return [c for _, c in scored[:limit]]
