"""Guards on the migration id sequence.

`apply_migrations` keys on the id string, so an id is a database-visible name,
not a label. These tests cover the two ways parallel branches corrupt that name
space — a duplicate, and a renamed id that already shipped.
"""

import tempfile

import pytest
from loregarden.db import migrations as M
from loregarden.db.migration_ids import (
    SHIPPED_MIGRATION_IDS,
    assert_migration_ids_are_sound,
)
from sqlalchemy import create_engine, text


def _noop(conn) -> None:
    pass


def test_the_real_list_is_sound():
    assert_migration_ids_are_sound([i for i, _ in M.MIGRATIONS])


def test_ledger_covers_every_migration():
    """The ledger must list every migration, not just an old prefix of them.

    Left to drift it protects less each release, and the id it stops covering is
    always the newest one — the one most likely to still be getting renumbered.
    Adding a migration therefore means adding its id here too. That is one line,
    and when two branches both add one the conflict is a visible two-line merge
    rather than a silent double-apply.
    """
    ids = [migration_id for migration_id, _ in M.MIGRATIONS]

    assert ids == list(SHIPPED_MIGRATION_IDS), (
        "MIGRATIONS and SHIPPED_MIGRATION_IDS disagree. If you added a migration, "
        "append its id to SHIPPED_MIGRATION_IDS as well."
    )


def test_duplicate_id_is_rejected(monkeypatch):
    """A merge that keeps both sides of a collided number."""
    monkeypatch.setattr(M, "MIGRATIONS", list(M.MIGRATIONS) + [(M.MIGRATIONS[-1][0], _noop)])

    with pytest.raises(RuntimeError, match="Duplicate migration id"):
        assert_migration_ids_are_sound([i for i, _ in M.MIGRATIONS])


def test_renaming_a_shipped_id_is_rejected(monkeypatch):
    """The double-apply bug: the renamed migration re-runs where it already ran."""
    renamed = [
        (f"renamed_{i}" if i == SHIPPED_MIGRATION_IDS[-1] else i, f) for i, f in M.MIGRATIONS
    ]
    monkeypatch.setattr(M, "MIGRATIONS", renamed)

    with pytest.raises(RuntimeError, match="append-only"):
        assert_migration_ids_are_sound([i for i, _ in M.MIGRATIONS])


def test_dropping_a_shipped_id_is_rejected(monkeypatch):
    monkeypatch.setattr(M, "MIGRATIONS", list(M.MIGRATIONS)[:-1])

    with pytest.raises(RuntimeError, match="append-only"):
        assert_migration_ids_are_sound([i for i, _ in M.MIGRATIONS])


def test_appending_a_new_id_does_not_break_the_app(monkeypatch):
    """The runtime guard is a prefix check on purpose.

    A migration written but not yet added to the ledger must still let the app
    boot — otherwise writing one is impossible. `test_ledger_covers_every_migration`
    is what stops it *merging* that way.
    """
    monkeypatch.setattr(M, "MIGRATIONS", list(M.MIGRATIONS) + [("9999_brand_new", _noop)])

    assert_migration_ids_are_sound([i for i, _ in M.MIGRATIONS])


def test_renumbering_an_unshipped_id_is_allowed(monkeypatch):
    """Renumbering is the *correct* fix when main takes your number first —
    but only before the migration has reached anyone's database."""
    base = [(i, _noop) for i in SHIPPED_MIGRATION_IDS]
    monkeypatch.setattr(M, "MIGRATIONS", base + [("9998_mine", _noop)])
    assert_migration_ids_are_sound([i for i, _ in M.MIGRATIONS])

    monkeypatch.setattr(M, "MIGRATIONS", base + [("9999_mine", _noop)])
    assert_migration_ids_are_sound([i for i, _ in M.MIGRATIONS])


def test_a_renamed_migration_would_double_apply(monkeypatch):
    """Why the ledger exists, stated as behaviour rather than assertion.

    A guarded add-column no-ops on the second run, which is why this went
    unnoticed; a migration that rewrites data does not.
    """
    runs: list[int] = []

    def bump(conn) -> None:
        runs.append(1)
        conn.execute(text("UPDATE counter SET n = n + 1"))

    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/t.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE counter (n INTEGER)"))
        conn.execute(text("INSERT INTO counter VALUES (0)"))

    monkeypatch.setattr(M, "MIGRATIONS", [("0050_bump", bump)])
    M.apply_migrations(engine)

    monkeypatch.setattr(M, "MIGRATIONS", [("0053_bump", bump)])
    M.apply_migrations(engine)

    with engine.begin() as conn:
        assert conn.execute(text("SELECT n FROM counter")).scalar() == 2
    assert len(runs) == 2
