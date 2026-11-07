"""The API v1 contract: every path, method and parameter scripts may rely on, kept in docs/api/v1-contract.json.

    python -m webapp.api_contract           check the API against the snapshot (exit 1 on a difference)
    python -m webapp.api_contract --write   update the snapshot after an intended, compatible change

tests/unit/test_api_contract.py runs the check. A breaking change (a path, method or parameter removed, a parameter
made required, a body field removed) fails with what broke: such changes belong in /api/v2. An addition fails too,
until the snapshot is written, so every change to the contract is visible in review.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent / "docs" / "api" / "v1-contract.json"
METHODS = ("get", "post", "put", "patch", "delete")


def _body_fields(spec: dict, op: dict) -> dict[str, bool]:
    content = (op.get("requestBody") or {}).get("content", {})
    schema = (content.get("application/json") or {}).get("schema") or {}
    ref = schema.get("$ref", "")
    if ref:
        schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    required = set(schema.get("required", []))
    return {name: name in required for name in sorted(schema.get("properties", {}))}


def current() -> dict:
    """The contract of the code as it is: {path: {method: {"params": {name: required}, "body": {...}}}}."""
    from fastapi import FastAPI

    from webapp.api import router_v1
    app = FastAPI()
    app.include_router(router_v1)
    spec = app.openapi()
    out: dict = {}
    for path, item in sorted(spec["paths"].items()):
        for method in METHODS:
            op = item.get(method)
            if op is None:
                continue
            params = {p["name"]: bool(p.get("required")) for p in op.get("parameters", [])}
            entry = {"params": dict(sorted(params.items()))}
            body = _body_fields(spec, op)
            if body:
                entry["body"] = body
            out.setdefault(path, {})[method] = entry
    return out


def differences(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """(breaking, added) between two contracts."""
    breaking, added = [], []
    for path, methods in old.items():
        for method, entry in methods.items():
            now = new.get(path, {}).get(method)
            where = f"{method.upper()} {path}"
            if now is None:
                breaking.append(f"{where} was removed")
                continue
            for kind in ("params", "body"):
                before, after = entry.get(kind, {}), now.get(kind, {})
                for name, required in before.items():
                    if name not in after:
                        breaking.append(f"{where}: {kind[:-1] if kind == 'params' else 'body field'} {name!r} "
                                        "was removed")
                    elif after[name] and not required:
                        breaking.append(f"{where}: {name!r} is now required")
                for name, required in after.items():
                    if name not in before:
                        (breaking if required else added).append(
                            f"{where}: new {'required ' if required else ''}{name!r}")
    for path, methods in new.items():
        for method in methods:
            if method not in old.get(path, {}):
                added.append(f"{method.upper()} {path} was added")
    return breaking, added


def main(argv: list[str]) -> int:
    contract = current()
    if "--write" in argv:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(contract, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {SNAPSHOT} ({sum(len(m) for m in contract.values())} endpoints)")
        return 0
    old = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.is_file() else {}
    breaking, added = differences(old, contract)
    for line in breaking:
        print("BREAKING:", line)
    for line in added:
        print("added:", line)
    if added and not breaking:
        print("Compatible additions: run  python -m webapp.api_contract --write  and commit the snapshot.")
    return 1 if breaking or added else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
