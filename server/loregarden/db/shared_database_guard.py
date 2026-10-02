"""Keep an unmerged branch from migrating the shared database.

Every entry point migrates the database it opens — the server at boot, and the
CLI on every `mcp call`. The CLI, run from a linked worktree, opens the
*primary* checkout's database, so a branch that adds a migration applies it to
the live database the first time an agent on that branch reaches for the CLI.
That is how `0138_runtime_exit_actions` landed on 2026-09-16, during its own
ticket's implement run: live then carried an id main did not know, and
`refuse_stale_write` turned every write from a main build away for two weeks,
until agents learned to pass `--allow-stale` past it.

The rule: from a linked worktree, a migration may be applied to the primary
checkout's database only once `origin/main` ships it. The primary checkout
itself, sandboxes, test databases and anything main already has are untouched;
so is anything inside the worktree, which can itself sit inside the primary.
A ledger this cannot read refuses too — guessing "shipped" is the failure this
exists to stop.
"""

from __future__ import annotations

import ast
from pathlib import Path

from loregarden.db.migration_registry import ids_declared_in_source
from loregarden.services.git_subprocess import run_git

#: The ref whose ledger decides what has shipped.
SHIPPED_REF = "origin/main"
_LEDGER_PATH = "server/loregarden/db/migration_ids.py"
_LEDGER_NAME = "SHIPPED_MIGRATION_IDS"
_VERSIONS_DIR = "server/loregarden/db/versions"


class UnshippedMigrationError(RuntimeError):
    """A worktree build tried to migrate the shared database ahead of main."""


def _git_stdout(cwd: Path, *args: str) -> str:
    result = run_git(list(args), cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise UnshippedMigrationError(
            f"cannot tell which migrations {SHIPPED_REF} ships: "
            f"`git {' '.join(args)}` failed in {cwd}: {result.stderr.strip()}"
        )
    return result.stdout


def primary_checkout(code_root: Path) -> Path:
    """The checkout whose `.git` directory `code_root` shares."""
    common = Path(_git_stdout(code_root, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = code_root / common
    return common.resolve().parent


def _frozen_ledger_ids(code_root: Path, ref: str) -> frozenset[str]:
    source = _git_stdout(code_root, "show", f"{ref}:{_LEDGER_PATH}")
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.AnnAssign)  # py-org: allow-isinstance
            and isinstance(node.target, ast.Name)  # py-org: allow-isinstance
            and node.target.id == _LEDGER_NAME
            and node.value is not None
        ):
            return frozenset(ast.literal_eval(node.value))
    raise UnshippedMigrationError(f"{ref}:{_LEDGER_PATH} defines no {_LEDGER_NAME}")


def _versioned_ids(code_root: Path, ref: str) -> frozenset[str]:
    """Ids the `db/versions/` modules at ``ref`` declare.

    An empty listing is a real answer — a main that predates the package, or
    has no versioned migration yet — and git exits 0 for it.
    """
    listing = _git_stdout(
        code_root, "ls-tree", "--full-tree", "--name-only", ref, f"{_VERSIONS_DIR}/"
    )
    ids: set[str] = set()
    for path in listing.splitlines():
        if path.endswith(".py"):
            ids.update(ids_declared_in_source(_git_stdout(code_root, "show", f"{ref}:{path}")))
    return frozenset(ids)


def shipped_migration_ids(code_root: Path, ref: str = SHIPPED_REF) -> frozenset[str]:
    """Every migration id ``ref`` ships, read without checking anything out."""
    return _frozen_ledger_ids(code_root, ref) | _versioned_ids(code_root, ref)


def assert_may_migrate(database: Path | None, pending: list[str], *, code_root: Path) -> None:
    """Raise if applying ``pending`` here would migrate live data ahead of main."""
    if not pending or database is None:
        return
    primary = primary_checkout(code_root)
    worktree = code_root.resolve()
    if primary == worktree:
        return
    target = database.resolve()
    # Claude Code nests worktrees inside the primary (`.claude/worktrees/<name>`),
    # so a path under the primary is not enough to call it live: the worktree's
    # own files, such as the `task sandbox` copy in its data/, are the branch's.
    if not target.is_relative_to(primary) or target.is_relative_to(worktree):
        return
    unshipped = [mid for mid in pending if mid not in shipped_migration_ids(code_root)]
    if not unshipped:
        return
    raise UnshippedMigrationError(
        f"Refusing to apply {', '.join(unshipped)} to the shared database {database}: "
        f"this build runs from the worktree {code_root}, and {SHIPPED_REF} does not ship "
        "them yet. Applied now, live would carry ids main cannot read, and every write "
        "from a main build would be refused until this branch merges. Use the MCP tools "
        "(served by main's server), run the CLI from a checkout of main, or test against "
        "a copy (`task sandbox`). If it has merged, `git fetch origin` and retry."
    )
