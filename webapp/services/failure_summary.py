"""What went wrong in a run, in a few lines: the failed tests with where each stopped, and, when the failures are one
cause that belongs to the run rather than to any test (the browser would not start, the site is down, the login
fails), that cause said once, with what to do. Works on the rows of a results table (webapp/ui/test_details.py)."""
from __future__ import annotations

from dataclasses import dataclass, field

from webapp.services.failure_intel import FAILED, error_kind

SHOWN = 8  # failed tests listed; the table has the rest


@dataclass
class Shared:
    kind: str
    headline: str
    detail: str
    hint: str
    action: str = ""  # "headless": the button that turns Headless on


@dataclass
class Item:
    nodeid: str
    test_id: str
    name: str
    browser: str
    where: str
    message: str
    has_details: bool
    outcome: str


@dataclass
class Summary:
    failed: list[Item] = field(default_factory=list)
    shared: Shared | None = None
    more: int = 0  # failed tests not listed


def _first_line(message: str, limit: int = 220) -> str:
    text = next((line.strip() for line in str(message or "").splitlines() if line.strip()), "")
    return text[:limit]


def _tests(n: int) -> str:
    return f"{n} test" + ("" if n == 1 else "s")


def shared_cause(items: list[Item]) -> Shared | None:
    kinds = {error_kind(i.message)[0] for i in items}
    if len(kinds) != 1:
        return None
    kind, n = kinds.pop(), len(items)
    first = _first_line(items[0].message)
    if kind == "browser-start":
        return Shared(kind, f"The browser could not start, so {_tests(n)} did not run", first,
                      "On a machine with no screen a visible browser cannot open. Turn on Headless under "
                      "Browser & artifacts, then run again.", action="headless")
    if kind == "unreachable" and n >= 2:
        return Shared(kind, f"The site could not be reached ({_tests(n)})", first,
                      "Check the environment's base_url in systemData.json, the network, and any VPN or proxy.")
    if kind == "login" and n >= 2:
        return Shared(kind, f"Signing in failed ({_tests(n)})", first,
                      "Check the login credentials for this environment: Login credentials in Run setup, or .env.")
    return None


def summarize(rows: list[dict]) -> Summary | None:
    """None when nothing failed."""
    items = [Item(r.get("nodeid", ""), r.get("test_id", ""), r.get("name") or r.get("test_id", ""),
                  r.get("browser", ""), r.get("where", ""), _first_line(r.get("message", "")),
                  bool(r.get("has_details")), r.get("outcome", ""))
             for r in rows if r.get("outcome") in FAILED]
    if not items:
        return None
    return Summary(failed=items[:SHOWN], shared=shared_cause(items), more=max(len(items) - SHOWN, 0))
