"""Fetch missing login credentials from a secret store when a run starts, instead of keeping them in .env.

For each login value a system needs (the username_env / password_env names in systemData.json) that is not in the
environment, the .env or typed into the app, this asks the store for a secret with that name. The value is held in
memory for that run only (it is hidden from the console like a typed value) and never written anywhere.

Set it up in the workspace .env (or the environment):

    SECRET_PROVIDER=azure-keyvault      # needs the Azure CLI, signed in (az login)
    SECRET_AZURE_VAULT=my-vault         # the secret for NYBE_PASSWORD is read as NYBE-PASSWORD (Key Vault names
    SECRET_NAME_PREFIX=                 #   cannot have underscores); the prefix is put in front of that

    SECRET_PROVIDER=command             # any program that prints the secret: Windows Credential Manager through
    SECRET_COMMAND=["pwsh","-NoProfile","-Command","Get-Secret -Name '{name}' -AsPlainText"]   # SecretManagement,
                                        # 1Password / Bitwarden CLI, HashiCorp Vault ... {name} = the env var name

The program is run directly (no shell) with a 20 second limit; its standard output, without the final newline, is the
secret. An empty answer or a non-zero exit means "not found".
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

NAME_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TIMEOUT = 20
PROVIDERS = ("azure-keyvault", "command")


@dataclass
class Fetched:
    values: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # name -> why (never the secret)


class Vault:
    def __init__(self, lookup: Callable[[str], str | None], run=subprocess.run):
        self.get = lambda key: (lookup(key) or "").strip()
        self._run = run

    @property
    def provider(self) -> str:
        return self.get("SECRET_PROVIDER").lower()

    def configured(self) -> bool:
        return bool(self.provider)

    def blocker(self) -> str:
        """Why secrets cannot be fetched ("" = they can; also "" when no store is set up)."""
        provider = self.provider
        if not provider:
            return ""
        if provider not in PROVIDERS:
            return f"SECRET_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}."
        if provider == "azure-keyvault":
            if not self.get("SECRET_AZURE_VAULT"):
                return "Set SECRET_AZURE_VAULT to the Key Vault name."
            if not shutil.which("az"):
                return "The Azure CLI (az) was not found on this machine."
            return ""
        try:
            argv = self._command_template()
        except ValueError as e:
            return str(e)
        if not any("{name}" in part for part in argv):
            return "SECRET_COMMAND must contain {name}, which is replaced by the credential's name."
        return ""

    def _command_template(self) -> list[str]:
        raw = self.get("SECRET_COMMAND")
        try:
            argv = json.loads(raw)
        except ValueError:
            raise ValueError('SECRET_COMMAND must be a JSON list, e.g. ["program", "{name}"].') from None
        if not isinstance(argv, list) or not argv or not all(isinstance(p, str) for p in argv):
            raise ValueError('SECRET_COMMAND must be a JSON list of strings, e.g. ["program", "{name}"].')
        return argv

    def argv(self, name: str) -> list[str]:
        if not NAME_OK.match(name):
            raise ValueError(f"Not a credential name: {name!r}")
        if self.provider == "azure-keyvault":
            secret = self.get("SECRET_NAME_PREFIX") + name.replace("_", "-")
            argv = ["az", "keyvault", "secret", "show", "--vault-name", self.get("SECRET_AZURE_VAULT"),
                    "--name", secret, "--query", "value", "-o", "tsv"]
        else:
            argv = [part.replace("{name}", name) for part in self._command_template()]
        argv[0] = shutil.which(argv[0]) or argv[0]  # Windows finds az.cmd and pwsh.exe this way
        return argv

    def fetch(self, names: list[str]) -> Fetched:
        out = Fetched()
        reason = self.blocker()
        for name in names:
            if reason:
                out.errors[name] = reason
                continue
            try:
                done = self._run(self.argv(name), capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=TIMEOUT, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                out.errors[name] = f"The secret store did not answer within {TIMEOUT} seconds."
                continue
            except (OSError, ValueError) as e:
                out.errors[name] = f"Could not run the secret command: {type(e).__name__}"
                continue
            value = done.stdout[:-1] if done.stdout.endswith("\n") else done.stdout
            value = value[:-1] if value.endswith("\r") else value
            if done.returncode != 0 or not value:
                first = (done.stderr or "").strip().splitlines()[:1]
                out.errors[name] = ("not found" if done.returncode == 0 else
                                    f"the store said: {first[0][:160]}" if first else "not found")
                continue
            out.values[name] = value
        return out


def missing_credentials(status: list[dict] | None, have: dict[str, str]) -> list[str]:
    """Names a system needs that are neither set nor already supplied."""
    return [c["key"] for c in status or [] if not c["set"] and not have.get(c["key"])]
