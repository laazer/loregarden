"""Loregarden's templates for local instances, built on `lore_eden.instances`.

Two templates, one per way of testing a change:

- **client** — a Vite dev client from any worktree, proxied to a server
  instance: main for a UI-only change, a branch server otherwise. The proxy
  target reaches Vite as ``LOREGARDEN_API_TARGET`` (see `client/vite.config.ts`).
- **server** — a control plane from any worktree, on its own port and its own
  database: a snapshot of main's (the default) or a fresh one.

A branch server runs in sandbox mode. Its snapshot describes main's live agent
runs and worktrees, and an ordinary boot acts on that description — adopting
run processes, failing runs it thinks were interrupted, pruning worktrees,
resuming orchestrations. In a snapshot every one of those would be done to
main's machinery. See `Settings.sandbox`.

Worktrees are offered as choices from ``git worktree list`` of the repo root
rather than accepted as free text: the parameter becomes a working directory
and a ``LOREGARDEN_REPO_ROOT``, and anything outside this repository's own
worktrees has no business being either.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from lore_eden.instances import (
    FileInstanceRegistry,
    InstanceKind,
    InstanceManager,
    InstanceRecord,
    LaunchContext,
    LaunchRequest,
    LaunchSpec,
    SelfRegistration,
    TemplateCatalog,
    TemplateInfo,
    TemplateParam,
    TemplateParamError,
    register_self,
    resolve_params,
)
from loregarden.config import settings
from loregarden.services.git_subprocess import run_git
from loregarden.services.path_resolve import resolve_sqlite_path, sqlite_url_for_path

logger = logging.getLogger(__name__)

PROJECT = "loregarden"
MAIN_TARGET = "main"
SERVER_PORT_RANGE = (8100, 8199)
CLIENT_PORT_RANGE = (5180, 5299)
#: A fresh worktree syncs its virtualenv before uvicorn starts.
SERVER_READY_TIMEOUT_SECONDS = 300.0
#: A fresh worktree runs `npm ci` before Vite starts.
CLIENT_READY_TIMEOUT_SECONDS = 600.0


class DatabaseMode(StrEnum):
    """What a branch server starts from."""

    #: A copy of main's database, taken at launch.
    SNAPSHOT = "snapshot"
    #: An empty database, schema'd and seeded at boot.
    FRESH = "fresh"


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str


def list_worktrees(repo_root: Path) -> list[Worktree]:
    """This repository's worktrees, the primary checkout first."""
    result = run_git(
        ["worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # The primary checkout is still launchable; a worktree picker that
        # offers only it is degraded, not wrong. Said, not swallowed.
        logger.warning("git worktree list failed in %s: %s", repo_root, result.stderr.strip())
        return [Worktree(repo_root, "")]
    trees: list[Worktree] = []
    path: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch ") and path is not None:
            trees.append(Worktree(path, line.removeprefix("branch refs/heads/")))
            path = None
        elif line == "detached" and path is not None:
            trees.append(Worktree(path, "(detached)"))
            path = None
    return trees or [Worktree(repo_root, "")]


def _worktree_param() -> tuple[TemplateParam, dict[str, Worktree]]:
    trees = {str(tree.path): tree for tree in list_worktrees(settings.repo_root)}
    return (
        TemplateParam(
            key="worktree",
            label="Worktree",
            description="The checkout to run. Commit or not — it runs what is on disk.",
            required=True,
            default=str(settings.repo_root),
            choices=list(trees),
        ),
        trees,
    )


def snapshot_database(target: Path) -> None:
    """A consistent copy of main's database, taken while main keeps writing.

    Through SQLite's backup API rather than a file copy: a copy of a WAL
    database taken mid-write can be torn, and one missing its ``-wal`` file is
    missing whatever has not been checkpointed yet.
    """
    source = resolve_sqlite_path(settings.database_url, settings.repo_root)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


class ServerTemplate:
    def _info(self) -> tuple[TemplateInfo, dict[str, Worktree]]:
        worktree, trees = _worktree_param()
        info = TemplateInfo(
            name="server",
            kind=InstanceKind.SERVER,
            description="A control plane from a worktree, on its own port and database (sandboxed).",
            params=[
                worktree,
                TemplateParam(
                    key="database",
                    label="Database",
                    description="A snapshot of main's data, or an empty database.",
                    default=DatabaseMode.SNAPSHOT,
                    choices=list(DatabaseMode),
                ),
            ],
        )
        return info, trees

    def describe(self) -> TemplateInfo:
        return self._info()[0]

    def build(self, request: LaunchRequest, ctx: LaunchContext) -> LaunchSpec:
        info, trees = self._info()
        values = resolve_params(info, request.params)
        tree = trees[values["worktree"]]
        server_dir = tree.path / "server"
        if not (server_dir / "pyproject.toml").is_file():
            raise TemplateParamError(f"{tree.path} has no server/pyproject.toml to run")
        db_path = ctx.instance_dir / "loregarden.db"
        if DatabaseMode(values["database"]) == DatabaseMode.SNAPSHOT:
            snapshot_database(db_path)
        return LaunchSpec(
            project=PROJECT,
            name=request.name or f"server-{tree.branch or tree.path.name}",
            kind=InstanceKind.SERVER,
            command=[
                "uv",
                "run",
                "--project",
                str(server_dir),
                "uvicorn",
                "loregarden.main:app",
                "--host",
                "{host}",
                "--port",
                "{port}",
            ],
            cwd=server_dir,
            env={
                "LOREGARDEN_REPO_ROOT": str(tree.path),
                "LOREGARDEN_DATABASE_URL": sqlite_url_for_path(db_path),
                "LOREGARDEN_SANDBOX": "1",
                "LOREGARDEN_MCP_URL": "{url}/mcp",
                "LOREGARDEN_DEV_PORT": "{port}",
                # Main's memory lives outside the database; a sandbox that
                # inherited these would write its experiments into it.
                "LOREGARDEN_OBSIDIAN_VAULT_DIR": "",
                "LOREGARDEN_MEMORY_SQLITE_URL": "",
            },
            port_range=SERVER_PORT_RANGE,
            health_path="/health",
            ready_timeout_seconds=SERVER_READY_TIMEOUT_SECONDS,
            labels={
                "branch": tree.branch,
                "worktree": str(tree.path),
                "database": values["database"],
            },
        )


class ClientTemplate:
    def _info(self, registry: FileInstanceRegistry) -> tuple[TemplateInfo, dict[str, Worktree]]:
        worktree, trees = _worktree_param()
        branch_servers = [
            record.id
            for record in registry.scan(PROJECT).records
            if record.kind == InstanceKind.SERVER and record.managed and registry.is_alive(record)
        ]
        info = TemplateInfo(
            name="client",
            kind=InstanceKind.CLIENT,
            description="A dev client from a worktree, pointed at main or at a branch server.",
            params=[
                worktree,
                TemplateParam(
                    key="target",
                    label="Server",
                    description="main for a UI-only change; a branch server for a server change.",
                    default=MAIN_TARGET,
                    choices=[MAIN_TARGET, *branch_servers],
                ),
            ],
        )
        return info, trees

    def describe(self) -> TemplateInfo:
        return self._info(get_registry())[0]

    def build(self, request: LaunchRequest, ctx: LaunchContext) -> LaunchSpec:
        info, trees = self._info(ctx.registry)
        values = resolve_params(info, request.params)
        tree = trees[values["worktree"]]
        client_dir = tree.path / "client"
        if not (client_dir / "package.json").is_file():
            raise TemplateParamError(f"{tree.path} has no client/package.json to run")
        target = _resolve_target(ctx.registry, values["target"])
        return LaunchSpec(
            project=PROJECT,
            name=request.name or f"client-{tree.branch or tree.path.name}",
            kind=InstanceKind.CLIENT,
            command=[
                "sh",
                "-c",
                'test -d node_modules || npm ci; exec npm run dev -- --host "$1" --port "$2" --strictPort',
                "vite",
                "{host}",
                "{port}",
            ],
            cwd=client_dir,
            env={"LOREGARDEN_API_TARGET": target.url},
            port_range=CLIENT_PORT_RANGE,
            # Vite answers its index as soon as it is listening.
            health_path="/",
            ready_timeout_seconds=CLIENT_READY_TIMEOUT_SECONDS,
            target_instance_id=target.id,
            labels={"branch": tree.branch, "worktree": str(tree.path), "api": target.url},
        )


def _resolve_target(registry: FileInstanceRegistry, target: str) -> InstanceRecord:
    record = registry.find_main(PROJECT) if target == MAIN_TARGET else registry.get(target)
    if record is None or not registry.is_alive(record):
        if target == MAIN_TARGET:
            raise TemplateParamError(
                "No main server is registered. Start it with `task server` "
                "(it registers itself), or point the client at a branch server."
            )
        raise TemplateParamError(f"Server {target} is no longer running")
    return record


@lru_cache(maxsize=1)
def get_registry() -> FileInstanceRegistry:
    return FileInstanceRegistry()


@lru_cache(maxsize=1)
def get_instance_manager() -> InstanceManager:
    catalog = TemplateCatalog()
    catalog.register(ClientTemplate())
    catalog.register(ServerTemplate())
    return InstanceManager(get_registry(), catalog)


def register_main() -> SelfRegistration | None:
    """Advertise this process as loregarden's main server, when it is a dev server.

    Only `scripts/dev-server.sh` says which port it bound (``LOREGARDEN_DEV_PORT``);
    a test process or the packaged sidecar does not, and advertising a port
    nobody knows would send every client to the wrong place. A launched
    branch server is registered by its launcher, and `register_self` knows
    to leave that record alone.
    """
    if settings.dev_port is None or settings.sandbox:
        return None
    try:
        return register_self(
            get_registry(),
            project=PROJECT,
            name="main",
            kind=InstanceKind.SERVER,
            host=settings.dev_host,
            port=settings.dev_port,
            labels={"worktree": str(settings.repo_root)},
        )
    except OSError:
        # Discovery is a convenience beside the control plane, not part of
        # it: an unwritable home directory must not stop the server booting.
        # Logged, so a client that cannot find main has a reason on record.
        logger.exception("could not register this server in the local instance registry")
        return None
