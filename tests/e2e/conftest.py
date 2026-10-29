"""End-to-end fixtures: a local web page and a SMOKE workspace, so real runs need no external site.

Needs Playwright's Chromium (`python -m playwright install chromium`). Run with: python -m pytest tests/e2e
"""
from __future__ import annotations

import functools
import shutil
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def site_url():
    """The smoke page, served on 127.0.0.1 for the whole session."""
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES / "site"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def workspace(tmp_path: Path, site_url: str, monkeypatch) -> Path:
    """Fresh copy of the SMOKE workspace pointing at the local page; no repo .env is read."""
    ws = tmp_path / "workspace"
    shutil.copytree(FIXTURES / "workspace", ws)
    system_data = ws / "Data" / "systemData.json"
    system_data.write_text(system_data.read_text(encoding="utf-8").replace("http://127.0.0.1:{PORT}", site_url),
                           encoding="utf-8")
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setenv("AUTOMATENY_WORKSPACE", str(ws))
    monkeypatch.setenv("AUTOMATENY_DOTENV_PATH", str(empty_env))
    return ws
