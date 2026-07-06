import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from loregarden.services.path_resolve import (
    expand_path,
    resolve_icloud_root,
    resolve_sqlite_path,
)


def _find_repo_root() -> Path:
    env = os.environ.get("LOREGARDEN_REPO_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "agent_context").is_dir() and (parent / "server").is_dir():
            return parent
    return here.parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOREGARDEN_")

    repo_root: Path = _find_repo_root()
    database_url: str = "sqlite:///data/loregarden.db"
    agent_context_dir: Path = Path("agent_context")
    project_board_dir: Path = Path("project_board")
    workflow_templates_dir: Path = Path("agent_context/workflows")
    cli_adapter: str = "local"
    claude_model: str = ""
    cursor_model: str = ""
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = ""
    claude_permission_mode: str = "default"
    claude_output_format: str = "text"
    cursor_output_format: str = "text"
    allow_permission_bypass: bool = False
    permission_approval_timeout_seconds: float = 3600.0
    triage_timeout_seconds: int = 300
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Optional shared-secret bearer token. When set, all /api and /mcp requests
    # must present it (Authorization: Bearer <token> or X-Loregarden-Token).
    # Empty (default) keeps the zero-config local dev flow with auth disabled.
    api_token: str = ""
    # Filesystem ceiling for the workspace path browser / importer. Empty
    # defaults to the user's home directory (legacy behaviour). Set this to a
    # narrower directory (e.g. your projects folder) to restrict how far the
    # unauthenticated browse/import endpoints can read.
    browse_root: str = ""
    # iCloud + Obsidian memory (optional — empty disables external memory backends)
    icloud_root: str = ""
    obsidian_vault_dir: str = ""
    obsidian_memory_subdir: str = "Loregarden/Memory"
    obsidian_learnings_subdir: str = "Loregarden/Learnings"
    # Structured memory graph SQLite (optional; defaults under iCloud when vault is set)
    memory_sqlite_url: str = ""
    # CI Integration settings
    ci_webhook_secret: str = ""  # GitHub webhook secret (empty disables signature verification)
    ci_retry_limit: int = 3  # Max auto-fix attempts per CI failure
    ci_enabled: bool = True  # Feature flag
    ci_log_retention_days: int = 30  # How long to keep CI logs
    ci_auto_fix_timeout: int = 600  # 10 min timeout for fix agent
    # Parallel Execution settings
    max_parallel_agents: int = 3  # Max concurrent agent runs (2-5)
    worktree_cleanup_delay_hours: int = 1  # Auto-cleanup after merge
    parallel_enabled: bool = True  # Feature flag for parallel execution

    @field_validator("database_url", "memory_sqlite_url", mode="before")
    @classmethod
    def _strip_sqlite_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


settings = Settings()
settings.agent_context_dir = settings.repo_root / settings.agent_context_dir
settings.project_board_dir = settings.repo_root / settings.project_board_dir
settings.workflow_templates_dir = settings.repo_root / settings.workflow_templates_dir

from loregarden.services.memory_config import load_local_memory_config_into_settings  # noqa: E402

load_local_memory_config_into_settings()


def resolved_icloud_root() -> Path | None:
    return resolve_icloud_root(settings.icloud_root)


def resolved_obsidian_vault() -> Path | None:
    raw = settings.obsidian_vault_dir.strip()
    if not raw:
        return None
    path = expand_path(raw, repo_root=settings.repo_root)
    return path if path.is_dir() else None


def resolved_database_path() -> Path:
    return resolve_sqlite_path(settings.database_url, settings.repo_root)


def _memory_sqlite_base_path() -> Path | None:
    raw = settings.memory_sqlite_url.strip()
    if raw:
        return resolve_sqlite_path(raw, settings.repo_root)
    vault = resolved_obsidian_vault()
    icloud = resolved_icloud_root()
    if vault:
        return vault / "Loregarden" / "memory.db"
    if icloud:
        return icloud / "Loregarden" / "memory.db"
    return None


def resolved_memory_sqlite_path(workspace_slug: str = "") -> Path | None:
    base = _memory_sqlite_base_path()
    if not base:
        return None
    slug = workspace_slug.strip()
    if not slug:
        return base
    return base.parent / slug / base.name


def memory_backends_enabled() -> bool:
    return resolved_obsidian_vault() is not None or resolved_memory_sqlite_path() is not None
