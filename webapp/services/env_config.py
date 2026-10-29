"""Read/write the framework .env while preserving comments, order and unknown keys.

Secret keys (credentials) are never returned or written by this service; the UI only
shows whether they are set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SECRET_PATTERN = re.compile(r"(USERNAME|PASSWORD|SECRET|TOKEN|APIKEY|API_KEY)", re.IGNORECASE)


@dataclass(frozen=True)
class EnvField:
    key: str
    kind: str  # "str" | "bool" | "int" | "choice"
    default: str
    group: str
    label: str
    choices: tuple[str, ...] = ()


LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# Typed settings the Control tab edits (same keys the C# EnvConfigService handled)
FIELDS: tuple[EnvField, ...] = (
    EnvField("systemConfig", "str", "", "Run", "System"),
    EnvField("envConfig", "str", "", "Run", "Environment"),
    EnvField("runTags", "str", "", "Run", "Tags"),
    EnvField("browsers", "str", "chrome", "Run", "Browsers"),
    EnvField("headlessMode", "bool", "false", "Browser", "Headless"),
    EnvField("slowmo", "int", "0", "Browser", "Slow-mo (ms)"),
    EnvField("browserConfigMaximized", "bool", "true", "Browser", "Maximized"),
    EnvField("browserConfigWidth", "int", "1700", "Browser", "Width"),
    EnvField("browserConfigHeight", "int", "750", "Browser", "Height"),
    EnvField("pageWaitUntil", "choice", "load", "Browser", "Page wait until",
             ("load", "domcontentloaded", "networkidle", "commit")),
    EnvField("recording", "bool", "false", "Artifacts", "Record video"),
    EnvField("tracing", "bool", "false", "Artifacts", "Playwright trace"),
    EnvField("snapshots", "bool", "false", "Artifacts", "Snapshots"),
    EnvField("del_allure", "bool", "true", "Artifacts", "Clear old Allure results"),
    EnvField("LOG_LEVEL", "choice", "INFO", "Logging", "Log level", LEVELS),
    EnvField("TestSuiteLoggerLevel", "choice", "INFO", "Logging", "Suite logger", LEVELS),
    EnvField("PageLoggerLevel", "choice", "INFO", "Logging", "Page logger", LEVELS),
    EnvField("FormHandlerLoggerLevel", "choice", "INFO", "Logging", "Form logger", LEVELS),
)
FIELD_KEYS = {f.key for f in FIELDS}

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)$")


def is_secret(key: str) -> bool:
    return bool(SECRET_PATTERN.search(key))


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


class EnvConfigService:
    def __init__(self, env_path: Path):
        self.env_path = Path(env_path)

    def _read_lines(self) -> list[str]:
        if not self.env_path.exists():
            return []
        return self.env_path.read_text(encoding="utf-8-sig").splitlines()

    def _parse(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in self._read_lines():
            if line.lstrip().startswith("#"):
                continue
            m = _LINE.match(line)
            if m:
                values[m.group(1)] = _unquote(m.group(2))
        return values

    def load(self) -> dict[str, str]:
        """Typed, non-secret settings with defaults filled in."""
        raw = self._parse()
        return {f.key: raw.get(f.key, f.default) for f in FIELDS}

    def load_other(self) -> dict[str, str]:
        """Untyped non-secret keys (shown read-only)."""
        return {k: v for k, v in self._parse().items() if k not in FIELD_KEYS and not is_secret(k)}

    def is_set(self, key: str) -> bool:
        """True if the key has a non-empty value in .env. Never returns the value."""
        return bool(self._parse().get(key, "").strip())

    def save(self, updates: dict[str, object]) -> None:
        """Upsert typed keys; preserves every other line. Refuses secret/unknown keys."""
        bad = [k for k in updates if is_secret(k) or k not in FIELD_KEYS]
        if bad:
            raise ValueError(f"Refusing to write keys: {', '.join(bad)}")

        pending = {k: format_value(v) for k, v in updates.items()}
        lines = self._read_lines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            m = _LINE.match(line)
            if m and m.group(1) in pending:
                lines[i] = f"{m.group(1)}={pending.pop(m.group(1))}"
        lines.extend(f"{k}={v}" for k, v in pending.items())

        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.env_path.with_name(self.env_path.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(self.env_path)


def format_value(value: object) -> str:
    """Serialize a setting the way the framework's .env readers expect."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value if value is not None else "").replace("\n", " ").strip()
