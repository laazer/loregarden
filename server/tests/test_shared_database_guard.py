"""A worktree build may not migrate the shared database ahead of main.

`0138_runtime_exit_actions` reached the live database from its own ticket's
worktree two weeks before it merged; every write from a main build was refused
in between. These build a real primary checkout with a linked worktree and an
`origin/main` whose ledger ships one id, and ask the guard about each case.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest
from loregarden.cli import mcp_tools
from loregarden.cli import merge_queue as merge_queue_cli
from loregarden.cli.errors import EXIT_USAGE
from loregarden.cli.main import main as cli_main
from loregarden.db import migrations
from loregarden.db import session as db_session
from loregarden.db.shared_database_guard import (
    UnshippedMigrationError,
    assert_may_migrate,
    primary_checkout,
)
from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import MergeQueueEntry, MergeQueueState
from loregarden.services.git_subprocess import run_git
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine
from tests.db_templates import copy_database
from tests.factories import make_workspace

SHIPPED = "0001_shipped"
UNSHIPPED = "0002_branch_only"
SHIPPED_VERSIONED = "20261001_merged_already"
UNSHIPPED_VERSIONED = "20261002_branch_only"


def _git(cwd: Path, *args: str) -> None:
    run_git(
        ["-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkouts(tmp_path: Path) -> tuple[Path, Path]:
    """(primary, worktree): origin/main ships SHIPPED; live data sits in primary."""
    primary = tmp_path / "primary"
    ledger = primary / "server/loregarden/db/migration_ids.py"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(f'SHIPPED_MIGRATION_IDS: tuple[str, ...] = (\n    "{SHIPPED}",\n)\n')
    (ledger.parent / "versions").mkdir()
    (ledger.parent / "versions/merged_already.py").write_text(
        "from loregarden.db.versions import migration\n\n\n"
        f'@migration("{SHIPPED_VERSIONED}", after="{SHIPPED}")\n'
        "def m_merged_already(conn):\n    pass\n"
    )
    _git(tmp_path, "init", "-q", "-b", "main", str(primary))
    _git(primary, "add", ".")
    _git(primary, "commit", "-q", "-m", "ledger")
    _git(primary, "update-ref", "refs/remotes/origin/main", "HEAD")
    (primary / "data").mkdir()
    (primary / "data/loregarden.db").touch()
    worktree = tmp_path / "worktree"
    _git(primary, "worktree", "add", "-q", "-b", "ticket", str(worktree))
    return primary.resolve(), worktree.resolve()


def test_the_fixture_is_what_the_guard_reads(checkouts):
    primary, worktree = checkouts
    assert primary_checkout(worktree) == primary
    assert primary_checkout(primary) == primary


def test_a_worktree_may_not_apply_an_unshipped_migration_to_live(checkouts):
    primary, worktree = checkouts

    with pytest.raises(UnshippedMigrationError, match=UNSHIPPED) as refused:
        assert_may_migrate(primary / "data/loregarden.db", [SHIPPED, UNSHIPPED], code_root=worktree)

    assert SHIPPED not in str(refused.value).split("Refusing to apply ")[1].split(" to ")[0]


def test_a_worktree_may_apply_what_main_ships(checkouts):
    primary, worktree = checkouts
    assert_may_migrate(primary / "data/loregarden.db", [SHIPPED], code_root=worktree)


def test_a_versioned_migration_on_main_counts_as_shipped(checkouts):
    """`db/versions/` ids are read from main's source, not from the closed ledger."""
    primary, worktree = checkouts
    assert_may_migrate(primary / "data/loregarden.db", [SHIPPED_VERSIONED], code_root=worktree)


def test_a_worktree_may_not_apply_an_unshipped_versioned_migration_to_live(checkouts):
    primary, worktree = checkouts

    with pytest.raises(UnshippedMigrationError, match=UNSHIPPED_VERSIONED):
        assert_may_migrate(
            primary / "data/loregarden.db",
            [SHIPPED_VERSIONED, UNSHIPPED_VERSIONED],
            code_root=worktree,
        )


def test_the_primary_checkout_migrates_its_own_database(checkouts):
    primary, _ = checkouts
    assert_may_migrate(primary / "data/loregarden.db", [UNSHIPPED], code_root=primary)


def test_a_worktree_migrates_a_database_outside_the_primary_freely(checkouts, tmp_path):
    """Sandboxes, snapshots and test databases are the branch's to migrate."""
    _, worktree = checkouts
    assert_may_migrate(tmp_path / "copy/loregarden.db", [UNSHIPPED], code_root=worktree)
    assert_may_migrate(worktree / "data/sandbox/loregarden.db", [UNSHIPPED], code_root=worktree)


def test_an_unreadable_ledger_refuses_rather_than_guessing(checkouts):
    primary, worktree = checkouts
    _git(primary, "update-ref", "-d", "refs/remotes/origin/main")

    with pytest.raises(UnshippedMigrationError, match="cannot tell"):
        assert_may_migrate(primary / "data/loregarden.db", [SHIPPED], code_root=worktree)


def test_apply_migrations_consults_the_guard_before_migrating(tmp_path):
    """The seam: the registry's entry point is where every boot and CLI call lands."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    SQLModel.metadata.create_all(engine)
    refusal = UnshippedMigrationError("refused")

    with mock.patch.object(migrations, "assert_may_migrate", side_effect=refusal) as guard:
        with pytest.raises(UnshippedMigrationError):
            migrations.apply_migrations(engine)

    database, pending = guard.call_args.args
    assert database == tmp_path / "fresh.db"
    assert pending == [mid for mid, _ in migrations.MIGRATIONS]
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM schema_migrations").scalar() == 0


@pytest.fixture
def nested_worktree(checkouts) -> Path:
    """A worktree where Claude Code puts them: inside the primary checkout."""
    primary, _ = checkouts
    worktree = primary / ".claude/worktrees/ticket-nested"
    _git(primary, "worktree", "add", "-q", "-b", "nested", str(worktree))
    return worktree.resolve()


def test_a_nested_worktree_migrates_its_own_sandbox_copy(nested_worktree):
    """`task sandbox` copies live into the worktree's own data/; that copy is the branch's."""
    assert_may_migrate(
        nested_worktree / "data/sandbox/loregarden.db", [UNSHIPPED], code_root=nested_worktree
    )


def test_a_nested_worktree_still_may_not_migrate_live(checkouts, nested_worktree):
    primary, _ = checkouts

    with pytest.raises(UnshippedMigrationError, match=UNSHIPPED):
        assert_may_migrate(primary / "data/loregarden.db", [UNSHIPPED], code_root=nested_worktree)


def test_a_nested_worktree_may_not_migrate_another_worktrees_database(checkouts, nested_worktree):
    """Not live, but not this branch's either; the guard does not guess on someone else's data."""
    primary, worktree = checkouts
    sibling = primary / ".claude/worktrees/other/data/loregarden.db"

    with pytest.raises(UnshippedMigrationError, match=UNSHIPPED):
        assert_may_migrate(sibling, [UNSHIPPED], code_root=nested_worktree)


# --- test-break: the guard's contract, after S-8 makes it a wrapper ---------
#
# `primary_checkout` raises; `git_trees.main_working_tree` returns None. A
# wrapper that forgets the None is a `Path(None)` TypeError at a caller whose
# only handler is for `UnshippedMigrationError`, and the guard's whole purpose
# is that a topology it cannot read refuses rather than guesses.


def test_a_directory_with_no_repository_refuses_rather_than_guessing(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()

    with pytest.raises(UnshippedMigrationError):
        primary_checkout(outside)


def test_a_directory_that_is_gone_refuses_the_same_way(tmp_path):
    """The half of "could not look" that escapes as an OSError today, because
    `run_git` passes `cwd` to `subprocess.run` and git never starts.
    `git_reading` classifies it; the wrapper has to keep classifying it.
    """
    with pytest.raises(UnshippedMigrationError):
        primary_checkout(tmp_path / "vanished")


def test_the_guard_module_still_imports_on_its_own():
    """S-8's stated escape hatch, as a check rather than a hope: the dedup is
    only allowed if `db` importing *up* into `services` does not cycle.
    """
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-c", "import loregarden.db.shared_database_guard"],
        check=True,
        capture_output=True,
        text=True,
    )


# --- reads migrate nothing --------------------------------------------------
#
# `merge-queue wait` is what CLAUDE.md says to run right after enqueueing a PR,
# and a branch with a new migration is a normal thing to enqueue. It used to
# open the database through `init_db`, which migrates, so from that worktree
# the guard refused a command that had nothing to write.


@pytest.fixture
def live_from_worktree(checkouts, schema_template) -> Iterator[Engine]:
    """Live data in the primary, migrated to main; a worktree build that also carries UNSHIPPED."""
    primary, worktree = checkouts
    database = primary / "data/loregarden.db"
    database.unlink()
    copy_database(schema_template, database)
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE schema_migrations "
            "(id TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        for mid, _ in migrations.MIGRATIONS:
            conn.exec_driver_sql("INSERT INTO schema_migrations (id) VALUES (?)", (mid,))
    branch = [*migrations.MIGRATIONS, (UNSHIPPED, lambda conn: None)]
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(migrations, "MIGRATIONS", branch))
        stack.enter_context(mock.patch.object(db_session, "MIGRATIONS", branch))
        stack.enter_context(mock.patch.object(migrations, "_CODE_ROOT", worktree))
        stack.enter_context(mock.patch.object(db_session, "engine", engine))
        yield engine
    engine.dispose()


def _applied(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {row[0] for row in conn.exec_driver_sql("SELECT id FROM schema_migrations")}


def _merged_entry(engine: Engine) -> str:
    with Session(engine) as session:
        workspace = make_workspace(session, slug="queue-wait")
        entry = MergeQueueEntry(
            workspace_id=workspace.id,
            pr_number=977,
            pr_url="https://github.com/o/r/pull/977",
            pr_head_branch="claude/x",
            source_branch="claude/x",
            head_sha="a" * 40,
            position=1,
            state=MergeQueueState.MERGED,
            detail="landed",
        )
        session.add(entry)
        session.commit()
        return entry.id


def test_the_fixture_reproduces_the_refusal(live_from_worktree):
    """The control: migrating from this worktree is still refused."""
    with pytest.raises(UnshippedMigrationError, match=UNSHIPPED):
        db_session.init_db()


def test_merge_queue_wait_reads_the_entry_from_a_worktree_with_an_unshipped_migration(
    live_from_worktree, capsys
):
    entry_id = _merged_entry(live_from_worktree)

    with mock.patch.object(merge_queue_cli, "_server_up", return_value=True):
        code = cli_main(["merge-queue", "wait", entry_id, "--poll-seconds", "0"])

    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.startswith("merged")
    assert UNSHIPPED in err  # says it read without migrating
    assert UNSHIPPED not in _applied(live_from_worktree)


def test_a_read_only_mcp_call_reads_without_migrating(live_from_worktree):
    entry_id = _merged_entry(live_from_worktree)

    output = mcp_tools._run_tool(
        McpTool.MERGE_QUEUE_STATUS.value,
        {"entry_id": entry_id, "live": False},
        orchestrated=False,
        run_id="",
        allow_stale=False,
    )

    assert entry_id in output
    assert UNSHIPPED not in _applied(live_from_worktree)


def test_a_write_mcp_call_is_still_refused(live_from_worktree):
    with pytest.raises(UnshippedMigrationError, match=UNSHIPPED):
        mcp_tools._run_tool(
            McpTool.CREATE_TICKET.value, {}, orchestrated=False, run_id="", allow_stale=False
        )


def test_wait_on_a_database_never_migrated_exits_unreadable_not_stopped(live_from_worktree, capsys):
    with live_from_worktree.begin() as conn:
        conn.exec_driver_sql("DROP TABLE schema_migrations")

    with pytest.raises(SystemExit) as exited:
        cli_main(["merge-queue", "wait", "any-entry"])

    assert exited.value.code == merge_queue_cli.EXIT_WAIT_UNREADABLE
    assert "never been migrated" in capsys.readouterr().err


def test_wait_with_no_database_exits_unreadable_and_creates_none(tmp_path):
    missing = tmp_path / "gone/loregarden.db"
    engine = create_engine(f"sqlite:///{missing}")
    with mock.patch.object(db_session, "engine", engine):
        with pytest.raises(SystemExit) as exited:
            cli_main(["merge-queue", "wait", "any-entry"])

    assert exited.value.code == merge_queue_cli.EXIT_WAIT_UNREADABLE
    assert not missing.exists()


def test_wait_on_an_unknown_entry_is_a_usage_error_not_stopped(live_from_worktree):
    with mock.patch.object(merge_queue_cli, "_server_up", return_value=True):
        assert cli_main(["merge-queue", "wait", "no-such-entry"]) == EXIT_USAGE
