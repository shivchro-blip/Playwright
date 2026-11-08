"""Accounts for a shared web app: users, roles, hashed passwords and API tokens.

A user has a role everywhere, and can have a different role for one system, one environment, or one environment
of one system (grants): "editor, but only a viewer in production", "runner, and an editor on PORTAL". The most
specific grant that matches wins; an action without a system or environment uses the user's own role.

Login is off by default for the local, single-user app (AUTOPLAY_AUTH_ENABLED). Users live in
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
    "editor": {"edit", "record", "ai.write", "schedule", "delete", "signoff"},
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


def grant_matches(grant: dict, system: str | None, env: str | None) -> int:
    """How specifically a grant matches (system and environment: 3, system: 2, environment: 1, everywhere: 0);
    -1 when it does not match."""
    gs, ge = str(grant.get("system") or "*"), str(grant.get("env") or "*")
    if gs != "*" and gs != (system or None):
        return -1
    if ge != "*" and (env is None or ge.casefold() != env.casefold()):
        return -1
    return (2 if gs != "*" else 0) + (1 if ge != "*" else 0)


def check_grant(grant: dict) -> dict:
    system, env = str(grant.get("system") or "*").strip() or "*", str(grant.get("env") or "*").strip() or "*"
    role = str(grant.get("role") or "")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {', '.join(ROLES)}.")
    if system == "*" and env == "*":
        raise ValueError("A grant names a system, an environment or both (the role everywhere is the user's role).")
    for value in (system, env):
        if value != "*" and not NAME_RE.match(value):
            raise ValueError(f"{value!r}: letters, digits and . _ @ - only.")
    return {"system": system, "env": env, "role": role}


def describe_grant(grant: dict) -> str:
    where = " / ".join(x for x in (grant["system"] if grant["system"] != "*" else "",
                                    grant["env"] if grant["env"] != "*" else "") if x)
    return f"{grant['role']} on {where}"


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
    grants: list[dict] = field(default_factory=list)  # {system, env, role}: "*" is any
    sso: bool = False  # signs in through single sign-on (no password here)

    def role_for(self, system: str | None = None, env: str | None = None) -> str:
        """The role in a system and environment: the most specific matching grant, else the user's role."""
        best, role = -1, self.role
        for grant in self.grants:
            score = grant_matches(grant, system, env)
            if score >= 0 and score >= best:  # the most specific; of two equally specific, the later one
                best, role = score, grant.get("role", self.role)
        return role

    def can(self, permission: str, system: str | None = None, env: str | None = None) -> bool:
        return not self.disabled and allowed(self.role_for(system, env), permission)

    def may_somewhere(self, permission: str) -> bool:
        """Allowed by the user's role or by one of their grants (the action's own place is checked later)."""
        return not self.disabled and (allowed(self.role, permission)
                                      or any(allowed(str(g.get("role")), permission) for g in self.grants))


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

    def add_sso(self, username: str, role: str) -> User:
        """A user who signs in through single sign-on (no password here), added ahead of their first sign-in."""
        if not NAME_RE.match(username or ""):
            raise ValueError("User name: letters, digits and . _ @ - only (up to 64).")
        if role not in ROLES:
            raise ValueError(f"Role must be one of {', '.join(ROLES)}.")
        with self._lock:
            users = self._read()
            if username in users:
                raise ValueError(f"User {username!r} already exists.")
            users[username] = User(username, role, sso=True)
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

    def grant(self, username: str, role: str, system: str = "*", env: str = "*") -> User:
        """Give the user `role` in a system, an environment, or one environment of one system (replacing a grant
        for exactly that place)."""
        new = check_grant({"system": system, "env": env, "role": role})
        with self._lock:
            users = self._read()
            user = users.get(username)
            if not user:
                raise ValueError(f"No user {username!r}.")
            user.grants = [g for g in user.grants if (g.get("system", "*"), g.get("env", "*"))
                           != (new["system"], new["env"])] + [new]
            self._write(users)
        return user

    def ungrant(self, username: str, system: str = "*", env: str = "*") -> bool:
        with self._lock:
            users = self._read()
            user = users.get(username)
            if not user:
                raise ValueError(f"No user {username!r}.")
            kept = [g for g in user.grants if (g.get("system", "*"), g.get("env", "*"))
                    != ((system or "*").strip() or "*", (env or "*").strip() or "*")]
            changed = len(kept) != len(user.grants)
            user.grants = kept
            self._write(users)
        return changed

    def sso_user(self, username: str, role: str | None, grants: list[dict] | None, default_role: str = "") -> User:
        """The user who just signed in through single sign-on, created or updated. `role` and `grants` come from
        the identity provider's groups when it sends any that are mapped (it is then the source of truth);
        otherwise a known user keeps their role here and a new one gets `default_role` (none: refused)."""
        if not NAME_RE.match(username or ""):
            raise ValueError(f"The identity provider's user name {username!r} is not usable here.")
        with self._lock:
            users = self._read()
            user = users.get(username)
            if user is None:
                role = role or default_role
                if role not in ROLES:
                    raise PermissionError(f"{username} has no AutoPlay role: ask an admin to add you.")
                user = users[username] = User(username, role, sso=True)
            elif user.disabled:
                raise PermissionError(f"{username} is disabled.")
            elif role:
                user.role = role
            if role and role not in ROLES:
                raise ValueError(f"Role must be one of {', '.join(ROLES)}.")
            if grants is not None and (role or grants):
                user.grants = [check_grant(g) for g in grants]
            user.sso = True
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
