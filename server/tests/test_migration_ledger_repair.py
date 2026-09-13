"""The ledger heals its benign drift and refuses to guess at the rest.

Thirteen applied ids blocked every CLI write on 2026-09-13 through
`refuse_stale_write`, which is a plain set difference and cannot tell a
renumber from a deletion. Eleven were renumbers — the same body registered
under a new number, already applied under it too. Two were deletions: bodies
from branches that never merged. The doctor (`ledger_orphans`) classified them
correctly and warned; the guard refused anyway. These tests pin the repair.

Two rules, and they are deliberately different:

- A RENUMBERED orphan is pruned by the runner on every apply, without anyone
  deciding, because its row records nothing the new id does not.
- A DELETED orphan is never pruned by the runner. Its row is the only trace of
  what ran. Retiring it is a migration that names it — `0130` does, for the two
  that exist — so the decision has an author and a reason.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.db.migration_ledger import classify_orphans, prune_renumbered
from loregarden.db.migrations import MIGRATIONS, apply_migrations
from loregarden.db.migrations_runner import unknown_migration_ids
from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

REGISTERED = [mid for mid, _ in MIGRATIONS]
KNOWN = set(REGISTERED)


def _migrated_engine(tmp_path: Path, name: str):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)
    return engine


def _insert_ledger_row(engine, migration_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO schema_migrations (id) VALUES (:id)"), {"id": migration_id})


def _ledger(engine) -> set[str]:
    with engine.connect() as conn:
        return {row[0] for row in conn.execute(text("SELECT id FROM schema_migrations")).all()}


def _fake_old_id_for(registered_id: str) -> str:
    """The id a migration would have carried before a renumber: same suffix, number - 1."""
    number, _, suffix = registered_id.partition("_")
    return f"{int(number) - 1:04d}_{suffix}"


def test_classify_is_pure_and_names_both_kinds():
    real = REGISTERED[-1]
    renumbered = _fake_old_id_for(real)
    deleted = "0001_a_migration_this_build_has_never_heard_of"

    orphans = classify_orphans([*REGISTERED, renumbered, deleted], REGISTERED)

    assert {o.applied_id: o.renumbered_to for o in orphans} == {renumbered: real, deleted: ""}


def test_runner_prunes_a_renumbered_orphan_on_the_next_apply(tmp_path: Path, caplog):
    """The eleven. Applied under the old number, then under the new; old row goes."""
    engine = _migrated_engine(tmp_path, "renumbered.db")
    real = REGISTERED[-1]
    stale = _fake_old_id_for(real)
    _insert_ledger_row(engine, stale)
    assert stale in _ledger(engine)

    with caplog.at_level(logging.WARNING, logger="loregarden.db.migration_ledger"):
        apply_migrations(engine)

    assert stale not in _ledger(engine)
    assert real in _ledger(engine), "pruning must never touch the registered id"
    assert unknown_migration_ids(engine, KNOWN) == [], "the CLI guard is clean again"
    assert any(stale in r.getMessage() and real in r.getMessage() for r in caplog.records), (
        "a pruned row is named, not silently removed"
    )


def test_runner_never_prunes_a_deleted_orphan(tmp_path: Path):
    """The two. Nothing registered records what they did, so the row stays."""
    engine = _migrated_engine(tmp_path, "deleted.db")
    deleted = "0001_a_migration_this_build_has_never_heard_of"
    _insert_ledger_row(engine, deleted)

    apply_migrations(engine)

    assert deleted in _ledger(engine)
    assert unknown_migration_ids(engine, KNOWN) == [deleted], (
        "a real divergence still refuses writes — that is the guard working"
    )


def test_runner_keeps_an_old_id_whose_new_id_never_applied(tmp_path: Path):
    """Suffix match alone is not enough: prune only when the new id is applied.

    A database carrying the old id but NOT the new one has evidence the runner
    would otherwise erase — and the new id will apply on this very run, so the
    old row must survive until it has.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT)"
            )
        )
    real = REGISTERED[-1]
    stale = _fake_old_id_for(real)
    _insert_ledger_row(engine, stale)

    with engine.begin() as conn:
        pruned = prune_renumbered(conn, REGISTERED)

    assert pruned == [], "new id not applied yet — nothing to prune"
    assert stale in _ledger(engine)


def test_0130_retires_exactly_the_two_unmerged_ids_and_is_idempotent(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retire.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT)"
            )
        )
    for stale in (
        "0085_ticket_blocked_kind",
        "0116_stage_timeout_budgets",
        "0001_unrelated_orphan",
    ):
        _insert_ledger_row(engine, stale)

    apply_migrations(engine)

    ledger = _ledger(engine)
    assert "0085_ticket_blocked_kind" not in ledger
    assert "0116_stage_timeout_budgets" not in ledger
    assert "0001_unrelated_orphan" in ledger, "0130 names its two; it is not a sweep"
    assert "0130_retire_unmerged_branch_ledger_ids" in ledger

    apply_migrations(engine)
    assert _ledger(engine) == ledger, "second apply changes nothing"
