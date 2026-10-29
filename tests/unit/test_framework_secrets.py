"""Framework regression tests: secrets stay out of logs, downloads stay in their folder (no browser, no network)."""
from __future__ import annotations

import asyncio
import logging
import sys
import types
from contextlib import asynccontextmanager

import pytest

# pywinauto is Windows-only; executables imports it at module level
sys.modules.setdefault("pywinauto", types.SimpleNamespace(Application=object))
import pages.dynamic_page  # noqa: E402,F401  (same import order as the suite; utils.errors is circular)
import utils.api_helper as api_helper  # noqa: E402
import utils.executables as executables  # noqa: E402
from utils.context_manager import ContextManager  # noqa: E402

SECRET = "tok-1234567890-secret"


class _Response:
    status_code = 200
    text = f'{{"access_token": "{SECRET}"}}'

    def raise_for_status(self):
        pass

    def json(self):
        return {"access_token": SECRET}


def test_api_request_does_not_log_secrets(monkeypatch, caplog):
    endpoint = {"method": "POST", "url": "https://api.example/login",
                "headers": {"Authorization": "Bearer DICT.authToken"},
                "body": {"password": "DICT.pw"},
                "save": {"token": "access_token", "raw": "$raw"}}
    monkeypatch.setenv("envConfig", "dev")  # always set by the suite
    monkeypatch.setattr(api_helper, "load_api_endpoints", lambda: {"login": endpoint})
    monkeypatch.setattr(api_helper.requests, "post", lambda *a, **k: _Response())
    ctx = ContextManager()
    ctx.set("authToken", SECRET)
    ctx.set("pw", SECRET)

    with caplog.at_level(logging.DEBUG - 1):
        result = asyncio.run(api_helper.run_api_request("login", ctx))

    assert result == {"access_token": SECRET} and ctx.get("token") == SECRET  # behavior unchanged
    logged = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert SECRET not in logged
    assert "Authorization" in logged  # header names are still logged for debugging


def test_get_token_does_not_log_value(caplog):
    class Page:
        async def evaluate(self, script):
            return SECRET

    ctx = ContextManager()
    with caplog.at_level(logging.INFO):
        asyncio.run(api_helper.get_token(Page(), ctx))
    assert ctx.get("authToken") == SECRET
    assert SECRET not in caplog.text


@pytest.mark.parametrize("suggested, expected", [
    ("report.pdf", "report.pdf"),
    ("../../evil.txt", "evil.txt"),
    ("..\\..\\evil.txt", "evil.txt"),
    ("/etc/evil", "evil"),
    ("..", "download"),
    ("", "download"),
])
def test_download_stays_in_downloads_folder(suggested, expected):
    saved = []

    class Download:
        suggested_filename = suggested

        async def save_as(self, path):
            saved.append(path)

    class Info:
        @property
        def value(self):
            async def _v():
                return Download()
            return _v()

    class Locator:
        async def click(self):
            pass

    class Page:
        @asynccontextmanager
        async def expect_download(self):
            yield Info()

        def locator(self, _):
            return Locator()

    asyncio.run(executables.downloadFile({"locator": "a"}, None, Page()))
    assert saved == ["./reports/downloads/" + expected]
