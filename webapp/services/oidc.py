"""Single sign-on with OpenID Connect (Entra ID, Okta, Google Workspace, Keycloak, Auth0, ...).

The authorization code flow with PKCE: /login/sso sends the browser to the identity provider with a fresh state,
nonce and code challenge kept in the session; the provider sends it back to /login/sso/callback with a code, which
the app exchanges for tokens directly with the provider's token endpoint over TLS. The ID token comes straight from
the provider on that back channel, so (as OpenID Connect Core 3.1.3.7 allows) its issuer, audience, expiry and
nonce are checked, and TLS vouches for its origin instead of a signature check.

Who the person is comes from a claim (email by default, which must not be marked unverified). Their role can come
from the provider's groups:

    AUTOPLAY_OIDC_ROLE_MAP="qa-leads=admin, qa=editor, portal-team=PORTAL/*:editor, ops=*/prod:runner"

"group=role" sets the role everywhere; "group=SYSTEM/env:role" grants a role in one system, one environment
("*/prod") or one environment of one system. The highest role of the matched groups wins; a person in no mapped
group keeps the role an admin gave them here, or gets AUTOPLAY_OIDC_DEFAULT_ROLE, or is refused.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode

from webapp.services.users import ROLES

DISCOVERY_TTL = 3600
CLOCK_SKEW = 120


class SSOError(Exception):
    """Shown on the sign-in page as is."""


@dataclass
class RoleMap:
    roles: dict[str, str] = field(default_factory=dict)  # group -> role everywhere
    grants: dict[str, list[dict]] = field(default_factory=dict)  # group -> [{system, env, role}]

    @classmethod
    def parse(cls, text: str) -> "RoleMap":
        out = cls()
        for item in (text or "").replace(";", ",").split(","):
            item = item.strip()
            if not item:
                continue
            group, eq, target = item.rpartition("=")
            group, target = group.strip(), target.strip()
            if not eq or not group or not target:
                raise ValueError(f"Role map entry {item!r}: write group=role or group=SYSTEM/env:role.")
            if ":" in target:
                where, _, role = target.rpartition(":")
                system, _, env = where.partition("/")
                if role not in ROLES:
                    raise ValueError(f"Role map entry {item!r}: the role is one of {', '.join(ROLES)}.")
                out.grants.setdefault(group, []).append({"system": system.strip() or "*",
                                                         "env": env.strip() or "*", "role": role})
            else:
                if target not in ROLES:
                    raise ValueError(f"Role map entry {item!r}: the role is one of {', '.join(ROLES)}.")
                out.roles[group] = target
        return out

    @property
    def empty(self) -> bool:
        return not self.roles and not self.grants

    def resolve(self, groups: list[str]) -> tuple[str | None, list[dict] | None]:
        """(role everywhere, grants) for these groups; (None, None) when none of them is mapped."""
        mapped = [g for g in groups if g in self.roles or g in self.grants]
        if not mapped:
            return None, None
        roles = [self.roles[g] for g in mapped if g in self.roles]
        role = max(roles, key=ROLES.index) if roles else None
        best: dict[tuple[str, str], str] = {}
        for g in mapped:
            for grant in self.grants.get(g, []):
                key = (grant["system"], grant["env"])
                if key not in best or ROLES.index(grant["role"]) > ROLES.index(best[key]):
                    best[key] = grant["role"]
        return role, [{"system": s, "env": e, "role": r} for (s, e), r in best.items()]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt_claims(token: str) -> dict:
    """The payload of a JWT (its signature is not checked here: see the module docstring)."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise SSOError("The identity provider sent an ID token that cannot be read.") from e
    if not isinstance(claims, dict):
        raise SSOError("The identity provider sent an ID token that cannot be read.")
    return claims


@dataclass
class Identity:
    username: str
    groups: list[str]
    claims: dict


class OIDCClient:
    """One identity provider. `http` is (method, url, data, headers) -> (status, json); tests pass a fake."""

    def __init__(self, issuer: str, client_id: str, client_secret: str = "", *, scopes: str = "openid email profile",
                 username_claim: str = "email", groups_claim: str = "groups",
                 http: Callable[[str, str, dict | None, dict | None], tuple[int, dict]] | None = None):
        self.issuer = issuer.rstrip("/")
        self.client_id, self.client_secret = client_id, client_secret
        self.scopes = scopes if "openid" in scopes.split() else f"openid {scopes}".strip()
        self.username_claim, self.groups_claim = username_claim or "email", groups_claim or "groups"
        self.http = http or _httpx
        self._meta: dict | None = None
        self._meta_at = 0.0

    def metadata(self) -> dict:
        if self._meta is None or time.time() - self._meta_at > DISCOVERY_TTL:
            status, meta = self.http("GET", self.issuer + "/.well-known/openid-configuration", None, None)
            if status != 200 or not isinstance(meta, dict) or "authorization_endpoint" not in meta \
                    or "token_endpoint" not in meta:
                raise SSOError(f"Could not read the identity provider's configuration (HTTP {status}).")
            if str(meta.get("issuer", "")).rstrip("/") != self.issuer:
                raise SSOError("The identity provider's configuration names another issuer.")
            self._meta, self._meta_at = meta, time.time()
        return self._meta

    def start(self, redirect_uri: str) -> tuple[str, dict]:
        """(the URL to send the browser to, what to keep in the session until it comes back)."""
        verifier = _b64url(secrets.token_bytes(32))
        flow = {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24), "verifier": verifier,
                "redirect_uri": redirect_uri, "at": time.time()}
        query = urlencode({"response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri,
                           "scope": self.scopes, "state": flow["state"], "nonce": flow["nonce"],
                           "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
                           "code_challenge_method": "S256"})
        endpoint = self.metadata()["authorization_endpoint"]
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}", flow

    def finish(self, flow: dict | None, params: dict) -> Identity:
        """The person the provider vouched for, from the callback's query parameters."""
        if not flow or time.time() - float(flow.get("at", 0)) > 600:
            raise SSOError("The sign-in took too long or was started elsewhere. Try again.")
        if params.get("error"):
            raise SSOError(f"The identity provider refused: {params.get('error_description') or params['error']}.")
        if not params.get("state") or not secrets.compare_digest(str(params["state"]), str(flow["state"])):
            raise SSOError("The sign-in did not match the one started here. Try again.")
        if not params.get("code"):
            raise SSOError("The identity provider sent no code.")
        data = {"grant_type": "authorization_code", "code": params["code"], "redirect_uri": flow["redirect_uri"],
                "client_id": self.client_id, "code_verifier": flow["verifier"]}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        status, tokens = self.http("POST", self.metadata()["token_endpoint"], data,
                                   {"Accept": "application/json"})
        if status != 200 or not isinstance(tokens, dict) or not tokens.get("id_token"):
            detail = tokens.get("error_description") or tokens.get("error") if isinstance(tokens, dict) else ""
            raise SSOError(f"The identity provider did not give a token (HTTP {status})"
                           + (f": {detail}." if detail else "."))
        claims = self.check(jwt_claims(tokens["id_token"]), flow["nonce"])
        info_url = self.metadata().get("userinfo_endpoint")
        if info_url and tokens.get("access_token") and (self.username_claim not in claims
                                                        or self.groups_claim not in claims):
            info_status, info = self.http("GET", info_url, None,
                                          {"Authorization": f"Bearer {tokens['access_token']}"})
            if info_status == 200 and isinstance(info, dict) and info.get("sub") == claims.get("sub"):
                claims = {**info, **claims}
        return self.identity(claims)

    def check(self, claims: dict, nonce: str) -> dict:
        if str(claims.get("iss", "")).rstrip("/") != self.issuer:
            raise SSOError("The ID token was issued by another provider.")
        aud = claims.get("aud")
        if self.client_id not in (aud if isinstance(aud, list) else [aud]):
            raise SSOError("The ID token is for another application.")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") not in (None, self.client_id):
            raise SSOError("The ID token is for another application.")
        try:
            expires = float(claims.get("exp", 0))
        except (TypeError, ValueError):
            expires = 0
        if expires + CLOCK_SKEW < time.time():
            raise SSOError("The ID token has expired. Try again.")
        if not claims.get("nonce") or not secrets.compare_digest(str(claims["nonce"]), nonce):
            raise SSOError("The ID token does not belong to this sign-in. Try again.")
        return claims

    def identity(self, claims: dict) -> Identity:
        name = claims.get(self.username_claim)
        if not name or not isinstance(name, str):
            raise SSOError(f"The identity provider did not say who you are (no {self.username_claim!r}).")
        if self.username_claim == "email" and claims.get("email_verified") is False:
            raise SSOError("Your email address is not verified at the identity provider.")
        groups = claims.get(self.groups_claim) or []
        groups = [groups] if isinstance(groups, str) else [str(g) for g in groups if isinstance(g, (str, int))]
        return Identity(name.strip(), groups, claims)


def _httpx(method: str, url: str, data: dict | None, headers: dict | None) -> tuple[int, dict]:
    import httpx
    try:
        response = httpx.request(method, url, data=data, headers=headers, timeout=15)
    except httpx.HTTPError as e:
        raise SSOError(f"Could not reach the identity provider: {e}") from e
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body if isinstance(body, dict) else {}
