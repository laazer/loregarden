from loregarden.db.migrations import MIGRATIONS, apply_migrations
from sqlalchemy import text
from sqlmodel import SQLModel, create_engine


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def test_fresh_db_records_all_migrations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    # A fully-current schema created by SQLModel — migrations should still be
    # recorded (their guarded ALTERs are no-ops) so history is complete.
    SQLModel.metadata.create_all(engine)

    applied = apply_migrations(engine)
    assert applied == [mid for mid, _ in MIGRATIONS]

    with engine.connect() as conn:
        recorded = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}
    assert recorded == {mid for mid, _ in MIGRATIONS}


def test_migrations_are_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    SQLModel.metadata.create_all(engine)

    first = apply_migrations(engine)
    second = apply_migrations(engine)
    assert first  # ran the first time
    assert second == []  # nothing pending the second time


def test_old_schema_gets_upgraded(tmp_path):
    """A pre-migration database missing new columns is brought up to date."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE tickets (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '')")
        )

    assert "work_item_type" not in _columns(engine, "tickets")
    apply_migrations(engine)

    cols = _columns(engine, "tickets")
    assert "work_item_type" in cols
    assert "parent_ticket_id" in cols
    assert "permission_allowlist_json" in cols
