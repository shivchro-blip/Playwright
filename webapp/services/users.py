"""Accounts for a shared web app: users, roles, hashed passwords and API tokens.

Login is off by default for the local, single-user app (AUTOMATENY_AUTH_ENABLED). Users live in
<workspace>/reports/users.json (git-ignored), passwords as scrypt hashes and API tokens as SHA-256 hashes,
so the file never holds a usable secret. Manage them with:  python -m webapp.users --help
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROLES = ("viewer", "runner", "editor", "admin")
# What each role may do; a role can do everything the roles before it can
PERMISSIONS = {
    "viewer": {"view"},
    "runner": {"run", "check", "ai.read"},
    "editor": {"edit", "record", "ai.write", "schedule", "delete"},
    "admin": {"settings", "users"},
}
ROLE_LABELS = {"viewer": "Viewer: sees everything", "runner": "Runner: also starts and cancels runs",
               "editor": "Editor: also edits tests, pages and schedules", "admin": "Admin: also settings and users"}
NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
TOKEN_PREFIX = "any_"


def allowed(role: str, permission: str) -> bool:
    if role not in ROLES:
        return False
    granted: set[str] = set()
    for r in ROLES[:ROLES.index(role) + 1]:
        granted |= PERMISSIONS[r]
    return permission in granted


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r),
                                p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual.hex(), digest)


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_hex(8)))
    return _DUMMY[0]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class User:
    username: str
    role: str
    password_hash: str = ""
    tokens: list[dict] = field(default_factory=list)  # {id, name, hash, created}
    disabled: bool = False

    def can(self, permission: str) -> bool:
        return not self.disabled and allowed(self.role, permission)


LOCAL_USER = User("local", "admin")  # who acts when login is off


class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, User]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {u["username"]: User(**u) for u in data.get("users", [])}

    def _write(self, users: dict[str, User]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"users": [asdict(u) for u in users.values()]}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[User]:
        return sorted(self._read().values(), key=lambda u: u.username.casefold())

    def get(self, username: str) -> User | None:
        return self._read().get(username)

    def add(self, username: str, password: str, role: str) -> User:
        if not NAME_RE.match(username or ""):
            raise ValueError("User name: letters, digits and . _ @ - only (up to 64).")
        if role not in ROLES:
            raise ValueError(f"Role must be one of {', '.join(ROLES)}.")
        if len(password or "") < 10:
            raise ValueError("Use a password of at least 10 characters.")
        with self._lock:
            users = self._read()
            if username in users:
                raise ValueError(f"User {username!r} already exists.")
            users[username] = User(username, role, hash_password(password))
            self._write(users)
        return users[username]

    def update(self, username: str, *, password: str | None = None, role: str | None = None,
               disabled: bool | None = None) -> User:
        with self._lock:
            users = self._read()
            user = users.get(username)
            if not user:
                raise ValueError(f"No user {username!r}.")
            if password is not None:
                if len(password) < 10:
                    raise ValueError("Use a password of at least 10 characters.")
                user.password_hash = hash_password(password)
            if role is not None:
                if role not in ROLES:
                    raise ValueError(f"Role must be one of {', '.join(ROLES)}.")
                user.role = role
            if disabled is not None:
                user.disabled = disabled
            if user.role != "admin" or user.disabled:
                if not any(u.role == "admin" and not u.disabled for u in users.values()):
                    raise ValueError("Keep at least one active admin.")
            self._write(users)
        return user

    def remove(self, username: str) -> None:
        with self._lock:
            users = self._read()
            if username not in users:
                raise ValueError(f"No user {username!r}.")
            del users[username]
            if users and not any(u.role == "admin" and not u.disabled for u in users.values()):
                raise ValueError("Keep at least one active admin.")
            self._write(users)

    def authenticate(self, username: str, password: str) -> User | None:
        user = self.get(username or "")
        if not user:
            check_password(password or "", _dummy_hash())  # same cost as a real check: names don't leak
            return None
        if user.disabled or not check_password(password or "", user.password_hash):
            return None
        return user

    # ───── API tokens (scripts, CI) ─────

    def new_token(self, username: str, name: str = "token") -> str:
        """Create a token; it is shown once and only its hash is stored."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            users = self._read()
            user = users.get(username)
            if not user:
                raise ValueError(f"No user {username!r}.")
            user.tokens.append({"id": secrets.token_hex(4), "name": name[:40], "hash": _token_hash(token),
                                "created": datetime.now().isoformat(timespec="seconds")})
            self._write(users)
        return token

    def revoke_token(self, username: str, token_id: str) -> bool:
        with self._lock:
            users = self._read()
            user = users.get(username)
            if not user:
                return False
            kept = [t for t in user.tokens if t["id"] != token_id]
            changed = len(kept) != len(user.tokens)
            user.tokens = kept
            self._write(users)
        return changed

    def by_token(self, token: str) -> User | None:
        if not token.startswith(TOKEN_PREFIX):
            return None
        digest = _token_hash(token)
        for user in self._read().values():
            if not user.disabled and any(hmac.compare_digest(t["hash"], digest) for t in user.tokens):
                return user
        return None


def session_secret(path: Path) -> str:
    """A random secret for signing session cookies, created once and kept next to users.json."""
    path = Path(path)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value
