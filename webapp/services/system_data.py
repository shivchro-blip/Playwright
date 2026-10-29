"""systemData.json reader: systems, environments and which credential env vars they need."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoginInfo:
    system: str
    env: str
    base_url: str
    username_env: str
    password_env: str


class SystemDataService:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _systems(self) -> dict:
        if not self.path.exists():
            return {}
        with self.path.open(encoding="utf-8") as f:
            data = json.load(f)
        systems = data.get("systems", {})
        return systems if isinstance(systems, dict) else {}

    def systems(self) -> list[str]:
        return sorted(self._systems())

    def environments(self, system: str | None) -> list[str]:
        envs = self._systems().get(system or "", {})
        return list(envs) if isinstance(envs, dict) else []

    def login(self, system: str | None, env: str | None) -> LoginInfo | None:
        entry = self._systems().get(system or "", {}).get(env or "")
        if not isinstance(entry, dict):
            return None
        return LoginInfo(
            system=system or "",
            env=env or "",
            base_url=str(entry.get("base_url", "")),
            username_env=str(entry.get("username_env", "")),
            password_env=str(entry.get("password_env", "")),
        )
