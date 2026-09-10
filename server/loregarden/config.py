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
    #: Connections held open for the database pool, and the burst headroom above
    #: it. Together they must cover everything that can want a connection at
    #: once: one per in-flight sync request (FastAPI runs those in AnyIO's worker
    #: threadpool, forty threads by default), plus every ``/ws/queue`` snapshot,
    #: the reconciliation timer and run streaming.
    #:
    #: Sizing them below that does not shed load, it relocates the queue — from
    #: the threadpool, where waiting costs latency, to the connection pool, where
    #: it costs a thirty-second timeout and a failed request. The values these
    #: replace were SQLAlchemy's defaults, five and ten, which are meant for
    #: databases where a connection is a server-side process. A SQLite
    #: connection is a file handle; there is nothing to conserve by rationing it.
    db_pool_size: int = 20
    db_max_overflow: int = 40
    #: Seconds between reconciliation passes. Short enough that a wedged lane or
    #: a stranded stage clears itself well inside the time it takes an operator
    #: to notice; 0 or less turns the timer off and leaves repair to startup,
    #: which is the cadence this replaced.
    reconcile_interval_seconds: float = 30.0
    #: How long shutdown waits for in-flight agent runs to land before handing
    #: what is left to the interruption path. Short by default: a silent hang on
    #: shutdown is worse than the interruption it is trying to avoid. 0 disables
    #: the wait, restoring the pre-drain behaviour of exiting immediately.
    drain_timeout_seconds: float = 20.0
    agent_context_dir: Path = Path("agent_context")
    workflow_templates_dir: Path = Path("agent_context/workflows")
    cli_adapter: str = "local"
    claude_model: str = ""
    cursor_model: str = ""
    codex_model: str = ""
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: str = ""
    #: How many fresh-context iterations one LM Studio stage may take. Small
    #: local models drown in an ever-growing conversation long before a stage is
    #: done, so each iteration restarts from a prompt rebuilt out of the
    #: database rather than replaying the last one's messages. Bounded because an
    #: unbounded loop is the failure this replaces, not an improvement on it.
    lmstudio_max_iterations: int = 4
    opencode_model: str = ""
    claude_effort: str = ""
    cursor_effort: str = ""
    lmstudio_effort: str = ""
    opencode_effort: str = ""
    claude_permission_mode: str = "default"
    claude_output_format: str = "stream-json"
    cursor_output_format: str = "stream-json"
    allow_permission_bypass: bool = False
    permission_approval_timeout_seconds: float = 3600.0
    triage_timeout_seconds: int = 300
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    # Vite dev server origins, plus Tauri's fixed webview origins for the
    # packaged desktop app (tauri://localhost on macOS/Linux, the
    # http(s)://tauri.localhost variants on Windows) — none of these are
    # user-configurable, so it's safe to always allow them.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ]
    # Optional shared-secret bearer token. When set, all /api and /mcp requests
    # must present it (Authorization: Bearer <token> or X-Loregarden-Token).
    # Empty (default) keeps the zero-config local dev flow with auth disabled.
    api_token: str = ""
    # Filesystem ceiling for the workspace path browser / importer. Empty
    # defaults to the user's home directory (legacy behaviour). Set this to a
    # narrower directory (e.g. your projects folder) to restrict how far the
    # unauthenticated browse/import endpoints can read.
    browse_root: str = ""
    # Reference repos: third-party checkouts the ticket studio scoper reads
    # alongside the workspace repo. Empty defaults to ~/.loregarden/reference_repos.
    reference_repo_cache_dir: str = ""
    reference_repo_clone_timeout: int = 600
    # Fetch-through cache for reference MCP tools (wired by later tickets).
    reference_cache_ttl_seconds: int = 604800  # 7 days
    reference_fetch_timeout_seconds: float = 15.0
    reference_fetch_max_bytes: int = 5_000_000
    reference_fetch_max_redirects: int = 5
    # iCloud + Obsidian memory (optional — empty disables external memory backends)
    icloud_root: str = ""
    obsidian_vault_dir: str = ""
    obsidian_memory_subdir: str = "Loregarden/Memory"
    obsidian_learnings_subdir: str = "Loregarden/Learnings"
    obsidian_blogposts_subdir: str = "Loregarden/BlogPosts"
    obsidian_checkpoints_subdir: str = "Loregarden/Checkpoints"
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

    # Docker capacity ledger. The ceiling is derived from `docker info` unless
    # both overrides below are set; `headroom` is the fraction of the machine
    # this control plane is willing to book, and `reserved_*` is the standing
    # baseline of containers that no lease accounts for (a personal Postgres,
    # say). The ledger tracks leases only and must not pretend to attribute
    # load it did not grant.
    docker_capacity_enabled: bool = True
    docker_capacity_headroom: float = 0.75
    docker_capacity_cpus: float = 0.0  # override; 0 = derive from docker info
    docker_capacity_memory_mb: int = 0  # override; 0 = derive
    docker_capacity_max_leases: int = 4  # concurrency, the third dimension
    docker_reserved_cpus: float = 1.0
    docker_reserved_memory_mb: int = 2048
    docker_binary: str = "docker"
    docker_probe_timeout_seconds: float = 10.0
    docker_info_cache_seconds: float = 300.0
    docker_lease_ttl_seconds: int = 900
    docker_lease_max_ttl_seconds: int = 7200
    docker_lease_orphan_grace_seconds: int = 300
    docker_waiting_ttl_seconds: int = 600
    # An MCP reserve may wait inline this long before telling the caller to
    # poll. Capped hard: the call runs inside an agent's turn and spends the
    # run's wall-clock budget.
    docker_lease_inline_wait_max_seconds: float = 10.0
    # A stage that declared a footprint waits this long for capacity before its
    # run fails. The orchestrator is a background thread, so it may block where
    # an MCP handler may not.
    docker_stage_wait_seconds: float = 300.0
    # Wait estimation and poll back-pressure. The estimate is what makes the
    # poll interval honest — a fixed interval wastes calls on a long wait and
    # misses a short one — and the minimum interval is what stops a caller that
    # ignores the advice from turning the ledger into its own bottleneck.
    docker_wait_lookback_days: int = 30
    docker_poll_min_interval_seconds: float = 5.0
    docker_poll_max_interval_seconds: float = 120.0
    # Containers this machine runs that no lease will ever account for — a
    # personal database, a service left up on purpose. Named explicitly rather
    # than guessed at by age or naming convention: a standing container reported
    # as unaccounted on every sweep is noise, and noise is how a check stops
    # being read. Comma-separated.
    docker_baseline_projects: str = ""
    docker_baseline_containers: str = ""

    @field_validator("database_url", "memory_sqlite_url", mode="before")
    @classmethod
    def _strip_sqlite_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


settings = Settings()
settings.agent_context_dir = settings.repo_root / settings.agent_context_dir
settings.workflow_templates_dir = settings.repo_root / settings.workflow_templates_dir


def _prime_claude_oauth_token_env() -> None:
    """Make a cached `claude setup-token` token visible to every subprocess this
    process spawns (Baxter, CLI adapters) — not just this process's own HTTP
    calls — regardless of how the backend was launched. dev-server.sh exports
    this itself, but the Tauri desktop app spawns `python -m loregarden`
    directly and never runs that script, so it needs priming here instead.
    """
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip():
        return
    token_path = settings.repo_root / "data" / ".claude-oauth-token"
    if not token_path.is_file():
        return
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if token and token.isascii() and not any(ch.isspace() for ch in token):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token


def _prime_cursor_api_key_env() -> None:
    """Export Cursor auth for headless ``cursor-agent -p`` subprocesses.

    Prefer an explicit User API key file; otherwise reuse the Cursor IDE session
    token already on this machine (same store the Usage modal reads).
    """
    from loregarden.services.cursor_cli_auth import prime_cursor_api_key_env

    prime_cursor_api_key_env(repo_root=settings.repo_root)


_prime_claude_oauth_token_env()
_prime_cursor_api_key_env()

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
