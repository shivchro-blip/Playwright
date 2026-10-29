"""Single place to resolve the AutomateNY workspace (Data/, Pages/, Tests/, .env).

Resolution order:
  1. AUTOMATENY_WORKSPACE env var (set by the web app for each run)
  2. <repo>/workspace
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_WORKSPACE = REPO_ROOT / "workspace"


def resolve_workspace_dir() -> str:
    override = os.getenv("AUTOMATENY_WORKSPACE")
    if override:
        return override
    return str(REPO_WORKSPACE)


def resolve_dotenv_path(workspace_dir: str) -> str:
    override = os.getenv("AUTOMATENY_DOTENV_PATH")
    if override:
        return override
    workspace_env = os.path.join(workspace_dir, ".env")
    if os.path.exists(workspace_env):
        return workspace_env
    # Dev checkout keeps credentials in the repo-root .env
    return str(REPO_ROOT / ".env")


def resolve_systemdata_path(workspace_dir: str) -> str:
    override = os.getenv("AUTOMATENY_SYSTEMDATA_PATH")
    if override:
        return override
    return os.path.join(workspace_dir, "Data", "systemData.json")


def data_path(*parts: str) -> str:
    """Path inside <workspace>/Data."""
    return os.path.join(resolve_workspace_dir(), "Data", *parts)
