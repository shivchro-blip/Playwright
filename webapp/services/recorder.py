"""Test recorder: Playwright codegen in a real browser, turned into a page (fields) and a test case (steps).

The user clicks through the site in a headed browser opened by `playwright codegen`; when they close it,
the generated Python is parsed (never executed) with `ast` and mapped onto this framework's JSON:

- every element the recording touched becomes a page field, with the locator keys get_element understands
  (locator, role + name, label, placeholder, text, index);
- the actions become one `screen` step whose formData uses the engine's shorthands ("click", "check", ...).
  formData is a dict, so a field used again gets a pipe suffix ("q|", "q||"), which the engine reads as the same
  field; a second step would navigate to the page's path again and lose the page state.

Values typed into fields that look like credentials are dropped, so passwords never land in test JSON.
The feature is off unless `recorderEnabled=true` in .env, and it only runs when the app listens on
loopback: codegen opens its browser on the machine running the app.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from webapp.services.env_config import is_secret
from webapp.services.run_manager import RunManager

logger = logging.getLogger(__name__)

PAGE_ALIAS = re.compile(r"^page\d*$")
LOCATOR_METHODS = {"locator", "get_by_role", "get_by_text", "get_by_label", "get_by_placeholder",
                   "get_by_test_id", "get_by_alt_text", "get_by_title", "filter", "nth", "first", "last"}
# action method -> (field type when the field is new, formData value builder)
CLICK_TYPES = {"link": "link", "checkbox": "checkbox", "radio": "radio", "option": "button", "tab": "button"}
TYPE_SUFFIX = {"button": "Button", "link": "Link", "checkbox": "Checkbox", "radio": "Radio", "select": "Select",
               "document": "File"}
IGNORED_CALLS = {"close", "wait_for_load_state", "wait_for_timeout", "wait_for_url", "bring_to_front",
                 "set_viewport_size", "once", "on"}
SECRET_HINT = re.compile(r"pass(word|code)?|pwd|secret|token|otp|pin\b", re.IGNORECASE)


class RecorderError(Exception):
    """Shown to the user as is."""


@dataclass
class Recording:
    start_url: str = ""
    fields: dict[str, dict] = field(default_factory=dict)  # name -> field config
    steps: list[dict] = field(default_factory=list)  # formData dicts, one per screen step (normally one)
    warnings: list[str] = field(default_factory=list)
    actions: int = 0
    assertions: int = 0
    source: str = ""  # the generated code, with credential values masked

    def page(self, base_url: str = "") -> dict:
        """Page JSON for the recorded fields: path relative to base_url when the start URL is under it."""
        page: dict = {}
        if self.start_url:
            if base_url and self.start_url.startswith(base_url.rstrip("/")):
                page["path"] = self.start_url[len(base_url.rstrip("/")):] or "/"
            else:
                page["fullpath"] = self.start_url
        page["fields"] = dict(self.fields)
        return page


# ───────────── parsing ─────────────

def _literal(node: ast.AST, warnings: list[str]):
    """Constant value of an argument; re.compile("x") -> "x" (with a warning)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "compile" \
            and node.args:
        warnings.append("A regular-expression locator was saved as plain text; check it with the locator checker.")
        return _literal(node.args[0], warnings)
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _chain(node: ast.AST) -> tuple[str | None, list[tuple[str, list, dict]]]:
    """page.get_by_role("button", name="Go").first.click() -> ("page", [(get_by_role, args, kwargs), ...])."""
    parts: list[tuple[str, list, dict]] = []
    while True:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            parts.append((node.func.attr, list(node.args), {k.arg: k.value for k in node.keywords if k.arg}))
            node = node.func.value
        elif isinstance(node, ast.Attribute):
            parts.append((node.attr, [], {}))
            node = node.value
        elif isinstance(node, ast.Name):
            return node.id, list(reversed(parts))
        else:
            return None, list(reversed(parts))


def _quote(value: str, exact: bool = False) -> str:
    return json.dumps(value, ensure_ascii=False) + ("s" if exact else "i")


def _selector(method: str, args: list, kwargs: dict, warnings: list[str]) -> str | None:
    """One locator call as a Playwright selector string (the engine's own syntax), for chained locators."""
    first = _literal(args[0], warnings) if args else None
    exact = bool(_literal(kwargs["exact"], warnings)) if "exact" in kwargs else False
    if method == "locator":
        return str(first) if first is not None else None
    if method == "get_by_role":
        name = _literal(kwargs["name"], warnings) if "name" in kwargs else None
        return f"internal:role={first}" + (f"[name={_quote(str(name), exact)}]" if name is not None else "")
    if method == "get_by_text":
        return f"internal:text={_quote(str(first), exact)}"
    if method == "get_by_label":
        return f"internal:label={_quote(str(first), exact)}"
    if method == "get_by_placeholder":
        return f"internal:attr=[placeholder={_quote(str(first), exact)}]"
    if method == "get_by_alt_text":
        return f"internal:attr=[alt={_quote(str(first), exact)}]"
    if method == "get_by_title":
        return f"internal:attr=[title={_quote(str(first), exact)}]"
    if method == "get_by_test_id":
        return f"internal:testid=[data-testid={_quote(str(first), True)}]"
    if method == "filter" and "has_text" in kwargs:
        return f"internal:has-text={_quote(str(_literal(kwargs['has_text'], warnings)))}"
    if method == "nth":
        return f"nth={first}"
    if method == "first":
        return "nth=0"
    if method == "last":
        return "nth=-1"
    return None


def field_config(segments: list[tuple[str, list, dict]], warnings: list[str]) -> dict | None:
    """Locator calls -> field config with get_element's keys; chains fall back to one selector string."""
    segments = list(segments)
    index = None
    if segments and segments[-1][0] in ("first", "nth"):
        method, args, _ = segments.pop()
        index = 0 if method == "first" else _literal(args[0], warnings) if args else None
    cfg: dict | None = None
    if len(segments) == 1:
        method, args, kwargs = segments[0]
        value = _literal(args[0], warnings) if args else None
        if method == "locator" and value and not kwargs:
            cfg = {"locator": str(value)}
        elif method == "get_by_role" and value:
            cfg = {"role": str(value)}
            name = _literal(kwargs["name"], warnings) if "name" in kwargs else None
            if name not in (None, ""):
                cfg["name"] = str(name)
        elif method in ("get_by_label", "get_by_placeholder", "get_by_text") and value not in (None, ""):
            cfg = {method.removeprefix("get_by_"): str(value)}
        elif method == "get_by_test_id" and value:
            cfg = {"locator": f"[data-testid={json.dumps(str(value))}]"}
        elif method in ("get_by_alt_text", "get_by_title") and value:
            attr = "alt" if method == "get_by_alt_text" else "title"
            cfg = {"locator": f"[{attr}={json.dumps(str(value))}]"}
    if cfg is None and segments:
        parts = [_selector(m, a, k, warnings) for m, a, k in segments]
        if any(p is None for p in parts):
            return None
        cfg = {"locator": " >> ".join(parts)}  # type: ignore[arg-type]
    if cfg is None:
        return None
    if index is not None:
        cfg["index"] = int(index)
    return cfg


def _words(cfg: dict) -> list[str]:
    source = cfg.get("name") or cfg.get("label") or cfg.get("placeholder") or cfg.get("text") or ""
    if not source:
        loc = str(cfg.get("locator") or "")
        m = re.search(r'(?:#|name=|data-testid=|"|\')([A-Za-z][\w-]*)', loc)
        source = m.group(1) if m else ""
    return re.findall(r"[A-Za-z0-9]+", source)[:5]


def field_name(cfg: dict, kind: str, taken: set[str]) -> str:
    words = _words(cfg)
    base = (words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])) if words else kind
    if base[:1].isdigit():
        base = "f" + base
    suffix = TYPE_SUFFIX.get(kind, "")
    if suffix and not base.lower().endswith(suffix.lower()):
        base += suffix
    base = base[:40] or "field"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}{n}", n + 1
    return name


def _looks_secret(name: str, cfg: dict) -> bool:
    text = " ".join([name] + [str(v) for v in cfg.values()])
    return is_secret(name) or bool(SECRET_HINT.search(text))


def _statements(body: list[ast.stmt]):
    """Statements in order, descending into with-blocks (expect_popup, expect_download, ...)."""
    for stmt in body:
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            popup = any(isinstance(i.context_expr, ast.Call) and isinstance(i.context_expr.func, ast.Attribute)
                        and i.context_expr.func.attr == "expect_popup" for i in stmt.items)
            for inner, _ in _statements(stmt.body):
                yield inner, popup
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _statements(stmt.body)
        else:
            yield stmt, False


def parse_codegen(source: str) -> Recording:
    """Map `playwright codegen --target python` output onto fields and steps. The code is never run."""
    rec = Recording()
    warnings = rec.warnings
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise RecorderError(f"The recording could not be read: {e}") from e
    by_config: dict[str, str] = {}  # json(config) -> field name
    step: dict = {}  # formData, in action order
    secrets_masked: list[str] = []
    navigations = 0

    uses: dict[str, int] = {}
    last: list = [None, None, False]  # formData key, field name, field created by that action

    def add(name: str, value, created: bool = False) -> None:
        uses[name] = uses.get(name, 0) + 1
        key = name + "|" * (uses[name] - 1)  # the documented way to repeat a field: next, next|, next||
        step[key] = value
        last[:] = [key, name, created]

    for stmt, in_popup in _statements(tree.body):
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, (ast.Call, ast.Await)):
            continue
        call = stmt.value.value if isinstance(stmt.value, ast.Await) else stmt.value
        subject = call.func.value if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) else None
        if isinstance(subject, ast.Call) and isinstance(subject.func, ast.Name) and subject.func.id == "expect":
            rec.assertions += 1
            continue
        root, parts = _chain(call)
        if not root or not parts:
            continue
        if not PAGE_ALIAS.match(root):
            if root not in ("context", "browser"):
                warnings.append(f"Skipped a statement on '{root}' (line {stmt.lineno}).")
            continue
        method, args, kwargs = parts[-1]
        if method == "goto" and len(parts) == 1:
            url = _literal(args[0], warnings) if args else None
            navigations += 1
            if navigations == 1:
                rec.start_url = str(url or "")
            else:
                warnings.append(f"The recording navigated to {url} (line {stmt.lineno}); steps after it still use "
                                "this page. Move those fields to their own page if that URL is a different screen.")
            continue
        if parts[0][0] == "keyboard" and method == "press" and len(parts) == 2:
            key = _literal(args[0], warnings) if args else None
            target = last[1]
            if target and key:
                add(target, {"type": "keyboard", "value": str(key)})
                rec.actions += 1
            else:
                warnings.append(f"A key press ({key}) before any field was skipped (line {stmt.lineno}).")
            continue
        if method in IGNORED_CALLS or method.startswith("wait_for") or method.startswith("expect_"):
            continue
        if any(m in ("frame_locator", "content_frame") for m, _, _ in parts):
            warnings.append(f"Line {stmt.lineno} acts inside an iframe, which pages can't describe yet; skipped.")
            continue
        segments = parts[:-1]
        if not segments or any(m not in LOCATOR_METHODS for m, _, _ in segments):
            warnings.append(f"Skipped an unsupported call '{method}' (line {stmt.lineno}).")
            continue
        cfg = field_config(segments, warnings)
        if cfg is None:
            warnings.append(f"Couldn't express the locator on line {stmt.lineno} as a field; skipped.")
            continue
        if any(m == "last" for m, _, _ in segments):
            warnings.append(f"Line {stmt.lineno} uses .last; saved as a selector with nth=-1.")
        value_arg = _literal(args[0], warnings) if args else None
        role = cfg.get("role", "")
        if method in ("fill", "type", "press_sequentially"):
            kind, value = "text", "" if value_arg is None else value_arg
        elif method in ("click", "tap"):
            kind, value = CLICK_TYPES.get(role, "button"), "click"
        elif method == "dblclick":
            kind, value = "button", "dblclick"
        elif method == "hover":
            kind, value = "button", "hover"
        elif method in ("check", "set_checked"):
            kind, value = ("radio" if role == "radio" else "checkbox"), "check"
        elif method == "uncheck":
            kind, value = "checkbox", "click"
            warnings.append(f"Line {stmt.lineno} unchecks a box; saved as a click, so check its starting state.")
        elif method == "select_option":
            kind = "select"
            value = value_arg[0] if isinstance(value_arg, list) and value_arg else value_arg
        elif method == "press":
            kind, value = "text", {"type": "keyboard", "value": str(value_arg or "")}
        elif method == "set_input_files":
            kind, value = "document", value_arg[0] if isinstance(value_arg, list) and value_arg else value_arg
            warnings.append(f"Line {stmt.lineno} uploads a file from this machine ({value}); make sure the path "
                            "exists where the tests run.")
        elif method in ("focus", "clear", "scroll_into_view_if_needed"):
            continue
        else:
            warnings.append(f"Skipped an unsupported action '{method}' (line {stmt.lineno}).")
            continue

        key = json.dumps(cfg, sort_keys=True)
        name = by_config.get(key)
        created = name is None
        if created:
            name = field_name(cfg, kind, set(rec.fields))
            rec.fields[name] = {**cfg, "type": kind}
            by_config[key] = name
        elif method in ("fill", "type", "press_sequentially", "select_option") and last[1] == name \
                and step.get(last[0]) == "click":
            # codegen records the click that focused a field before typing into it: drop it
            del step[last[0]]
            uses[name] -= 1
            rec.actions -= 1
            if last[2]:
                rec.fields[name]["type"] = kind
                renamed = field_name(cfg, kind, set(rec.fields) - {name})
                if renamed != name:
                    rec.fields[renamed] = rec.fields.pop(name)
                    by_config[key] = name = renamed
        if in_popup:
            rec.fields[name]["waitForNewTab"] = True
        if method in ("fill", "type", "press_sequentially") and value not in ("", None) \
                and _looks_secret(name, rec.fields[name]):
            secrets_masked.append(str(value))
            warnings.append(f"The value typed into '{name}' looks like a credential and was not saved. "
                            "Use an ENV.<KEY> value from .env instead.")
            value = ""
        add(name, value, created)
        rec.actions += 1

    if step:
        rec.steps.append(step)
    if rec.assertions:
        warnings.append(f"{rec.assertions} assertion(s) were not converted: test cases have no assertion step. "
                        "Use the page's titleContains / errorContains checks instead.")
    if not rec.actions:
        warnings.append("No actions were recorded.")
    masked = source
    for secret in secrets_masked:
        masked = masked.replace(json.dumps(secret, ensure_ascii=False), '"••••••"')
    rec.source = masked
    return rec


# ───────────── merging into a page ─────────────

def merge_fields(existing: dict[str, dict], recorded: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """Add recorded fields to an existing page. A recorded field whose locator keys match an existing field
    reuses it; name clashes get a numeric suffix. Returns (fields, recorded name -> final name)."""
    locator_keys = ("locator", "role", "name", "label", "placeholder", "text", "contains", "index")

    def signature(cfg: dict) -> str:
        return json.dumps({k: cfg.get(k) for k in locator_keys if cfg.get(k) not in (None, "")}, sort_keys=True)

    fields = dict(existing)
    known: dict[str, str] = {}
    for n, c in existing.items():  # several fields can share a locator: reuse the first
        if isinstance(c, dict):
            known.setdefault(signature(c), n)
    renames: dict[str, str] = {}
    for name, cfg in recorded.items():
        sig = signature(cfg)
        if sig in known:
            renames[name] = known[sig]
            continue
        final, n = name, 2
        while final in fields:
            final, n = f"{name}{n}", n + 1
        fields[final] = cfg
        known[sig] = final
        renames[name] = final
    return fields, renames


def build_steps(screen: str, steps: list[dict], renames: dict[str, str] | None = None) -> list[dict]:
    renames = renames or {}
    return [{"screen": screen, "formData": {renames.get(k, k): v for k, v in s.items()}} for s in steps]


# ───────────── the codegen session ─────────────

@dataclass
class Session:
    url: str
    workdir: Path
    proc: subprocess.Popen | None = None
    status: str = "starting"  # starting | recording | finished | cancelled | failed
    error: str = ""
    recording: Recording | None = None

    @property
    def output(self) -> Path:
        return self.workdir / "recording.py"


class Recorder:
    """One codegen session at a time (it opens a real browser window on this machine)."""

    def __init__(self, python_exe: str = sys.executable, command: list[str] | None = None):
        # command: argv prefix that runs the Playwright CLI (tests swap in a fake)
        self.command = command or [python_exe, "-m", "playwright"]
        self.session: Session | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return bool(self.session and self.session.status in ("starting", "recording"))

    def start(self, url: str, storage_state: Path | None = None) -> Session:
        parts = urlsplit(url or "")
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise RecorderError("Enter a full http(s) URL to start recording from.")
        with self._lock:
            if self.active:
                raise RecorderError("A recording is already in progress. Close its browser window first.")
            workdir = Path(tempfile.mkdtemp(prefix="automateny-rec-"))
            session = Session(url=url, workdir=workdir)
            argv = [*self.command, "codegen", "--target", "python", "-o", str(session.output)]
            if storage_state and Path(storage_state).is_file():
                argv += ["--load-storage", str(storage_state)]
            argv.append(url)
            # Own process group, so cancelling also ends the Node driver and the browser it started
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" \
                else {"start_new_session": True}
            try:
                session.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                stdin=subprocess.DEVNULL, **group)
            except OSError as e:
                shutil.rmtree(workdir, ignore_errors=True)
                raise RecorderError(f"Could not start Playwright codegen: {e}") from e
            session.status = "recording"
            self.session = session
        threading.Thread(target=self._wait, args=(session,), name="recorder", daemon=True).start()
        return session

    def _wait(self, session: Session) -> None:
        proc = session.proc
        assert proc is not None
        out = proc.communicate()[0] or b""
        if session.status == "cancelled":
            shutil.rmtree(session.workdir, ignore_errors=True)
            return
        try:
            source = session.output.read_text(encoding="utf-8") if session.output.is_file() else ""
            if not source.strip():
                tail = out.decode("utf-8", "replace").strip()[-800:]
                raise RecorderError("The browser closed without a recording." + (f"\n{tail}" if tail else ""))
            session.recording = parse_codegen(source)
            session.status = "finished"
        except RecorderError as e:
            session.status, session.error = "failed", str(e)
        except Exception as e:  # never let the thread die silently
            logger.exception("Recording failed")
            session.status, session.error = "failed", f"{type(e).__name__}: {e}"
        finally:
            # The raw file can hold typed passwords; only the masked copy is kept (in memory)
            shutil.rmtree(session.workdir, ignore_errors=True)

    def cancel(self) -> None:
        session = self.session
        if session and session.proc and session.status in ("starting", "recording"):
            session.status = "cancelled"
            RunManager._kill_tree(session.proc)  # taskkill /T on Windows, killpg elsewhere


# ───────────── saving ─────────────

def suggest_page_name(url: str) -> str:
    """https://x/app/login.html -> LoginPage"""
    segment = [p for p in urlsplit(url or "").path.split("/") if p]
    words = re.findall(r"[A-Za-z0-9]+", (segment[-1].rsplit(".", 1)[0] if segment else "") or "home")
    name = "".join(w[:1].upper() + w[1:] for w in words)[:40] or "Home"
    if name[:1].isdigit():
        name = "Page" + name
    return name if name.endswith("Page") else name + "Page"


@dataclass
class SavedRecording:
    page_rel: str
    page_created: bool
    test_file: str
    test_index: int
    renames: dict[str, str]


def save_recording(pom, tests, system: str, recording: Recording, *, page_name: str, base_url: str,
                   test_id: str, test_file: str, env: str = "", tags: list[str] | None = None,
                   description: str = "") -> SavedRecording:
    """Write the recorded fields to a page (new, or merged into the existing page of that name) and append
    a test case whose steps use them. pom/tests: PomService/TestCaseService."""
    if not recording.fields:
        raise RecorderError("Nothing to save: the recording has no fields.")
    if not test_id.strip():
        raise RecorderError("Enter a test case ID.")
    page_name = page_name.strip().removesuffix(".json")
    existing_rel = pom.screen_index(system).get(page_name)
    if existing_rel:
        data, sha = pom.load(system, existing_rel)
        fields, renames = merge_fields(data.get("fields") or {}, recording.fields)
        pom.save(system, existing_rel, {**data, "fields": fields}, sha)
        rel, created = existing_rel, False
    else:
        rel = pom.create(system, "", page_name)  # validates the name
        _, sha = pom.load(system, rel)
        pom.save(system, rel, recording.page(base_url), sha)
        renames, created = {name: name for name in recording.fields}, True
    test_case = {"testCaseID": test_id.strip(), "description": description, "system": system, "env": env,
                 "module": "", "tags": list(tags or [system]),
                 "steps": build_steps(page_name, recording.steps, renames)}
    index, _ = tests.save(system, test_file, None, test_case, None)
    return SavedRecording(rel, created, test_file, index, renames)
