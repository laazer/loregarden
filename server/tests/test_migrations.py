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


def test_backfill_runs_against_a_populated_db(tmp_path):
    """Migrations must survive a database that actually has rows.

    The other tests here apply migrations to an empty schema, so the
    definition-versioning backfill loop never executed and its INSERTs were
    never checked against the real table constraints. On a populated database
    it failed on NOT NULL columns, taking the whole app down: migrations run in
    the lifespan hook, so the server bound its port and then served nothing.

    Note SQLModel.create_all wins the race with the migration's CREATE TABLE, and
    a Python field default renders as NOT NULL with no DDL default — so every
    column an INSERT omits must be supplied explicitly.
    """
    from datetime import datetime, timezone

    from loregarden.models.domain import StudioAgent, WorkflowTemplate
    from sqlmodel import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'populated.db'}")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            StudioAgent(
                id="agent-1",
                slug="populated-agent",
                name="Populated Agent",
                role_body="You do a thing.",
            )
        )
        session.add(
            WorkflowTemplate(
                id="tpl-1",
                slug="populated-template",
                name="Populated Template",
                stages_json="[]",
                transitions_json="[]",
                source_path="studio:populated-template",
            )
        )
        session.commit()

    apply_migrations(engine)

    with engine.connect() as conn:
        agent_versions = conn.execute(
            text("SELECT created_by, change_note, created_at FROM studio_agent_versions")
        ).fetchall()
        tpl_versions = conn.execute(
            text("SELECT created_by, change_note, created_at FROM workflow_template_versions")
        ).fetchall()

    assert len(agent_versions) == 1
    assert len(tpl_versions) == 1
    for created_by, change_note, created_at in agent_versions + tpl_versions:
        assert created_by == "migration"
        assert change_note == ""
        assert created_at is not None
        # Round-trips as a real timestamp rather than an empty string.
        datetime.fromisoformat(str(created_at)).astimezone(timezone.utc)
