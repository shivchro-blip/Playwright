"""Policy for the Python snippets in rand_gen_rules.json (custom_functions, dynamic_objects).

The snippets exist to generate test data, so they get a small, data-only surface:
- imports only from ALLOWED_MODULES (and from `os`, only `os.path`);
- no exec/eval/compile/open/__import__ and other reflective builtins;
- no attribute names starting with "_" (closes the usual dunder escapes such as ().__class__.__bases__);
- they run with restricted builtins whose __import__ enforces the same module list.

This is a guard rail, not a security boundary: Python can't be sandboxed in-process. It stops a snippet from
quietly reading files, starting processes or reaching the network. The file itself can only be changed on
disk (the web app has no endpoint for it), so treat it like code under review. To run snippets that need more,
set RANDGEN_ALLOW_UNSAFE_CODE=true in .env.
"""
from __future__ import annotations

import ast
import builtins
import os

ALLOWED_MODULES = {"random", "datetime", "string", "math", "re", "decimal", "uuid", "time", "calendar",
                   "itertools", "json", "faker", "os", "os.path"}
BLOCKED_NAMES = {"exec", "eval", "compile", "open", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                 "delattr", "input", "breakpoint", "exit", "quit", "memoryview", "help", "__builtins__",
                 "__loader__", "__spec__"}
SAFE_BUILTINS = {k: v for k, v in vars(builtins).items() if k not in BLOCKED_NAMES and not k.startswith("_")}


class CodePolicyError(ValueError):
    pass


def unsafe_allowed() -> bool:
    return os.environ.get("RANDGEN_ALLOW_UNSAFE_CODE", "").strip().lower() in ("1", "true", "yes", "on")


def check(code: str) -> None:
    """Raise CodePolicyError naming the first thing the snippet may not do."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # exec() reports it as the SyntaxError it is
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_MODULES:
                    raise CodePolicyError(f"line {node.lineno}: import of {alias.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_MODULES:
                raise CodePolicyError(f"line {node.lineno}: import from {node.module!r} is not allowed")
            if node.module == "os" and any(a.name != "path" for a in node.names):
                raise CodePolicyError(f"line {node.lineno}: only os.path may be imported from os")
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise CodePolicyError(f"line {node.lineno}: {node.id}() is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise CodePolicyError(f"line {node.lineno}: attribute {node.attr!r} is not allowed")
            if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr != "path":
                raise CodePolicyError(f"line {node.lineno}: os.{node.attr} is not allowed (only os.path)")


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002 - builtin signature
    if level or name not in ALLOWED_MODULES:
        raise ImportError(f"import of {name!r} is not allowed in autoGen code")
    return builtins.__import__(name, globals, locals, fromlist, level)


def namespace(**values) -> dict:
    """A globals dict for exec() with restricted builtins (unless unsafe code is allowed)."""
    if unsafe_allowed():
        return dict(values)
    return {"__builtins__": {**SAFE_BUILTINS, "__import__": _guarded_import}, **values}
