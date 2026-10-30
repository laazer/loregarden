"""The claim and evidence columns arrive by one append-only migration.

lg-external-harness-1026, R4. Five TEXT columns on `worktrees`: three that
carry the durable claim (`claim_pid`, `claim_birth`, `claim_boot_id`) and two
that carry what a refusal learned (`unresolved_reason`, `parked_at`).

There is deliberately **no receipt column**. The receipt is
`tickets.landed_sha` / `landed_branch`, which two writers already maintain; a
second record of "did this land" drifts from the first the day one of those
writers changes. See plan D1.

`after=` names the newest version id on `origin/main` **as read at implement
time** — it was `20261015_ux_answers_required` when this was planned, and the
test below reads it from the registry rather than hard-coding it, because
hard-coding it is how two branches claim the same slot.
"""

from __future__ import annotations

import pytest
from loregarden.db.migration_utils import table_columns
from loregarden.db.versions import worktree_claim_identity
from loregarden.models.domain import Worktree
from sqlalchemy import text

CLAIM_COLUMNS = {"claim_pid", "claim_birth", "claim_boot_id"}
EVIDENCE_COLUMNS = {"unresolved_reason", "parked_at"}
NEW_COLUMNS = CLAIM_COLUMNS | EVIDENCE_COLUMNS


def _legacy_worktrees_table(conn) -> None:
    """`worktrees` as it stood before this migration: none of the five columns."""
    conn.execute(text("DROP TABLE IF EXISTS worktrees"))
    conn.execute(
        text(
            """
            CREATE TABLE worktrees (
                id VARCHAR PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                agent_run_id VARCHAR NOT NULL,
                ticket_id VARCHAR,
                chat_session_id VARCHAR,
                parent_branch VARCHAR DEFAULT 'main',
                worktree_path VARCHAR DEFAULT '',
                state VARCHAR DEFAULT 'active',
                branch VARCHAR DEFAULT ''
            )
            """
        )
    )


def test_the_five_columns_are_added_to_an_old_table(isolated_db):
    with isolated_db.begin() as conn:
        _legacy_worktrees_table(conn)
        assert not (table_columns(conn, "worktrees") & NEW_COLUMNS)
        worktree_claim_identity.m_worktree_claim_identity(conn)
        assert NEW_COLUMNS <= table_columns(conn, "worktrees")


def test_applying_it_twice_is_a_no_op(isolated_db):
    """Append-only and re-runnable: a migration that is not is one that fails
    the second time a ledger repair replays it."""
    with isolated_db.begin() as conn:
        _legacy_worktrees_table(conn)
        worktree_claim_identity.m_worktree_claim_identity(conn)
        before = table_columns(conn, "worktrees")
        worktree_claim_identity.m_worktree_claim_identity(conn)
        assert table_columns(conn, "worktrees") == before


def test_a_row_written_before_the_migration_reads_back_as_empty_strings(isolated_db):
    """The fail-open AC7 bounds, at the schema level.

    An existing checkout must come through the migration with an *empty* claim,
    never a NULL that a `!=` comparison would make truthy and never a default
    that looks like a claim. Empty means "nobody recorded one", which is what
    `parse_claim` reads as no claimant.
    """
    with isolated_db.begin() as conn:
        _legacy_worktrees_table(conn)
        conn.execute(
            text(
                "INSERT INTO worktrees (id, workspace_id, agent_run_id, worktree_path) "
                "VALUES ('w1', 'ws1', 'r1', '/tmp/tree')"
            )
        )
        worktree_claim_identity.m_worktree_claim_identity(conn)
        row = conn.execute(
            text(
                "SELECT claim_pid, claim_birth, claim_boot_id, unresolved_reason, parked_at "
                "FROM worktrees WHERE id = 'w1'"
            )
        ).one()
    assert list(row) == ["", "", "", "", ""]


def test_it_adds_no_receipt_column(isolated_db):
    """Plan D1, asserted rather than remembered. The receipt is the landing
    record; a column here would be a second writer of the same fact."""
    with isolated_db.begin() as conn:
        _legacy_worktrees_table(conn)
        worktree_claim_identity.m_worktree_claim_identity(conn)
        columns = table_columns(conn, "worktrees")
    assert not {name for name in columns if "receipt" in name}
    assert "claim_tip_sha" not in columns, "plan D2: the tip is read live, not stored"


def test_a_partial_schema_is_left_alone(isolated_db):
    """The migration suite builds partial schemas; `create_all` adds the table
    whole. A migration that assumed its table exists fails those builds."""
    with isolated_db.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS worktrees"))
        worktree_claim_identity.m_worktree_claim_identity(conn)


def test_the_model_mirrors_every_column_the_migration_adds():
    """A migration whose model does not match is a column nothing reads."""
    fields = set(Worktree.model_fields)
    assert NEW_COLUMNS <= fields
    for name in NEW_COLUMNS:
        assert Worktree.model_fields[name].default == "", (
            f"{name} defaults to '' so an unwritten row is 'nobody said', not a value"
        )


def test_the_migration_id_is_registered_once_and_chains_onto_a_real_predecessor():
    """`after=` must name a migration that exists, and the id must be declared
    exactly once — a duplicate is how two branches claim one slot."""
    from loregarden.db.versions import REGISTRY

    entries = REGISTRY.registered()
    # `VersionedMigration.id`, not `.migration_id`: the registry's own field
    # name (lg-external-harness-1026 implement, AC18 — the assertion is
    # unchanged, only the attribute it reads).
    ids = [entry.id for entry in entries]
    assert ids.count(worktree_claim_identity.MIGRATION_ID) == 1

    mine = next(e for e in entries if e.id == worktree_claim_identity.MIGRATION_ID)
    assert mine.after in ids, (
        f"after={mine.after!r} names no registered migration — re-read the newest "
        "id on origin/main at implement time"
    )


@pytest.mark.parametrize("column", sorted(NEW_COLUMNS))
def test_every_new_column_is_text(isolated_db, column: str):
    with isolated_db.begin() as conn:
        _legacy_worktrees_table(conn)
        worktree_claim_identity.m_worktree_claim_identity(conn)
        info = {row[1]: row[2] for row in conn.execute(text("PRAGMA table_info(worktrees)"))}
    assert info[column].upper().startswith("TEXT")
