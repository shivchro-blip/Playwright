"""Remote runners: machines that run tests for this app.

A runner is a small agent (python -m webapp.runner_agent) on another machine. It calls this app over HTTPS,
so it needs no open port and works behind a firewall or proxy. Each runner has a name and a token. The token is
shown once and only its SHA-256 hash is kept, in <workspace>/reports/runners.json (git-ignored). Manage them with
    python -m webapp.runners add NAME | list | remove NAME
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
import threading
import time
from pathlib import Path

TOKEN_PREFIX = "apr_"
NAME_OK = re.compile(r"^[A-Za-z0-9][\w\-]{0,39}$")
PROTOCOL = 1  # bump when the runner and the app must be updated together
ONLINE_SECONDS = 30  # a runner that has not called in this long shows as offline


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RunnerStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}  # name -> time.monotonic() of the last call (memory only)

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def names(self) -> list[str]:
        return sorted(self._read())

    def add(self, name: str) -> str:
        """Register a runner and return its token (shown once)."""
        if not NAME_OK.match(name or ""):
            raise ValueError("A runner name has letters, digits, '_' or '-' (at most 40 characters).")
        with self._lock:
            data = self._read()
            if name in data:
                raise ValueError(f"A runner named {name!r} already exists.")
            token = TOKEN_PREFIX + secrets.token_urlsafe(32)
            data[name] = {"hash": _hash(token), "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self._write(data)
        return token

    def remove(self, name: str) -> bool:
        with self._lock:
            data = self._read()
            if name not in data:
                return False
            del data[name]
            self._write(data)
            self._seen.pop(name, None)
        return True

    def by_token(self, token: str) -> str | None:
        """The runner name a token belongs to, or None."""
        if not token.startswith(TOKEN_PREFIX):
            return None
        digest = _hash(token)
        found = None
        for name, info in self._read().items():  # compare against every hash: no early exit to time
            if hmac.compare_digest(str(info.get("hash", "")), digest):
                found = name
        return found

    def touch(self, name: str) -> None:
        self._seen[name] = time.monotonic()

    def online(self, name: str) -> bool:
        seen = self._seen.get(name)
        return seen is not None and time.monotonic() - seen <= ONLINE_SECONDS

    def list(self) -> list[dict]:
        return [{"name": n, "created": info.get("created", ""), "online": self.online(n)}
                for n, info in sorted(self._read().items())]


def main(argv: list[str] | None = None) -> int:
    from webapp.settings import get_settings

    parser = argparse.ArgumentParser(prog="python -m webapp.runners", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add", help="register a runner and print its token").add_argument("name")
    sub.add_parser("remove", help="remove a runner (its token stops working)").add_argument("name")
    sub.add_parser("list", help="list runners")
    args = parser.parse_args(argv)
    store = RunnerStore(get_settings().resolved_runs_dir.parent / "runners.json")
    if args.cmd == "list":
        for r in store.list():
            print(f"{r['name']}  (added {r['created']})")
        return 0
    if args.cmd == "remove":
        print("Removed." if store.remove(args.name) else f"No runner named {args.name!r}.")
        return 0
    try:
        token = store.add(args.name)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"Runner {args.name!r} added. Token (shown once, keep it secret):\n\n  {token}\n")
    print("On the runner machine:\n"
          f"  python -m webapp.runner_agent --server https://<this app> --token {token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
