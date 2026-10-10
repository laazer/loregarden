"""A worktree build may not migrate the shared database ahead of main.

`0138_runtime_exit_actions` reached the live database from its own ticket's
worktree two weeks before it merged; every write from a main build was refused
in between. These build a real primary checkout with a linked worktree and an
`origin/main` whose ledger ships one id, and ask the guard about each case.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from loregarden.db import migrations
from loregarden.db.shared_database_guard import (
    UnshippedMigrationError,
    assert_may_migrate,
    primary_checkout,
)
from loregarden.services.git_subprocess import run_git
from sqlmodel import SQLModel, create_engine

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
