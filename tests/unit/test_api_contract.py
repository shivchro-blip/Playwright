"""API v1: the contract scripts rely on does not change by accident, and /api and /api/v1 are the same API."""
import json

from webapp import api_contract
from webapp.api import API_VERSION, install_api


def test_the_api_matches_its_committed_contract():
    old = json.loads(api_contract.SNAPSHOT.read_text(encoding="utf-8"))
    breaking, added = api_contract.differences(old, api_contract.current())
    assert not breaking, "Breaking API v1 changes (they belong in /api/v2):\n" + "\n".join(breaking)
    assert not added, ("API v1 additions not in the snapshot; run  python -m webapp.api_contract --write  and "
                       "commit docs/api/v1-contract.json:\n" + "\n".join(added))


def test_differences_tell_breaking_from_compatible():
    old = {"/api/v1/runs": {"get": {"params": {"system": False}}, "post": {"params": {}, "body": {"env": True}}}}
    same = json.loads(json.dumps(old))
    assert api_contract.differences(old, same) == ([], [])
    new = {"/api/v1/runs": {"get": {"params": {"system": True, "limit": False, "env": True}},
                            "post": {"params": {}, "body": {}}},
           "/api/v1/meta": {"get": {"params": {}}}}
    breaking, added = api_contract.differences(old, new)
    assert "GET /api/v1/runs: 'system' is now required" in breaking
    assert "GET /api/v1/runs: new required 'env'" in breaking
    assert "POST /api/v1/runs: body field 'env' was removed" in breaking
    assert added == ["GET /api/v1/runs: new 'limit'", "GET /api/v1/meta was added"]
    breaking, _ = api_contract.differences(old, {})
    assert sorted(breaking) == ["GET /api/v1/runs was removed", "POST /api/v1/runs was removed"]


def test_every_v1_path_is_versioned():
    paths = set(api_contract.current())
    assert len(paths) > 30 and all(p.startswith(f"/api/v{API_VERSION}/") for p in paths)


def test_both_names_answer_with_the_version_header(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from webapp import settings as settings_module
    from webapp import state as state_module
    monkeypatch.setenv("AUTOPLAY_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTOPLAY_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("AUTOPLAY_DOTENV_PATH", str(tmp_path / "empty.env"))
    (tmp_path / "empty.env").write_text("", encoding="utf-8")
    settings_module.get_settings.cache_clear()
    state_module.services.cache_clear()
    try:
        app = FastAPI()
        install_api(app)
        client = TestClient(app)
        v1, legacy = client.get("/api/v1/meta"), client.get("/api/meta")
        assert v1.status_code == legacy.status_code == 200 and v1.json() == legacy.json()
        assert v1.json()["api_version"] == API_VERSION and v1.json()["base"] == "/api/v1"
        assert v1.headers["X-API-Version"] == legacy.headers["X-API-Version"] == "1"
        assert client.get("/api/v1/runs").json() == client.get("/api/runs").json() == []
        schema_paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/runs" in schema_paths and "/api/runs" not in schema_paths  # the docs show v1
    finally:
        settings_module.get_settings.cache_clear()
        state_module.services.cache_clear()
