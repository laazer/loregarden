"""Foreign keys are enforced at runtime, and repaired on the way in.

SQLite defaults enforcement off, per connection. For most of this project's
life the declared references were documentation: a delete path that missed a
child table left rows pointing at nothing, and a column that named the wrong
kind of id was never contradicted.
"""

from loregarden.db.migrations import apply_migrations
from loregarden.db.migrations_fk_repair import m_repair_dangling_references
from loregarden.models.domain import Artifact, Ticket, Workspace
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine


def _fresh_engine(tmp_path, name: str):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    SQLModel.metadata.create_all(engine)
    return engine


def test_every_connection_enforces_foreign_keys(tmp_path):
    """Not just the app's own engine — a pragma only some connections set makes
    enforcement depend on which code path opened the connection."""
    engine = _fresh_engine(tmp_path, "enforced.db")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_a_reference_to_a_missing_row_is_rejected(tmp_path):
    engine = _fresh_engine(tmp_path, "reject.db")
    with Session(engine) as session:
        session.add(Ticket(external_id="orphan", workspace_id="no-such-workspace", title="Orphan"))
        try:
            session.commit()
        except Exception as exc:
            assert "FOREIGN KEY constraint failed" in str(exc)
        else:  # pragma: no cover - only reached if enforcement regressed
            raise AssertionError("a ticket in a workspace that does not exist was accepted")


def test_migrations_run_with_enforcement_off_and_restore_it(tmp_path):
    """`relax_not_null` rebuilds a table by dropping it and renaming a copy over
    it. Every dependant's reference breaks in between, so the run needs the
    pragma off — and the connection must not go back to the pool that way.
    """
    engine = _fresh_engine(tmp_path, "migrated.db")
    applied = apply_migrations(engine)

    assert applied, "expected a fresh database to apply the full ledger"
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_repair_nulls_what_it_can_and_drops_what_it_cannot(tmp_path):
    """The repair runs off `foreign_key_check`, so it fixes what an install
    actually has rather than what this checkout's schema declares.
    """
    engine = _fresh_engine(tmp_path, "repair.db")
    with Session(engine) as session:
        workspace = Workspace(slug="ws", name="ws")
        session.add(workspace)
        session.commit()
        ticket = Ticket(external_id="t", workspace_id=workspace.id, title="T")
        session.add(ticket)
        session.commit()
        ticket_id = ticket.id

    # Both orphans written behind enforcement's back, the way the unenforced
    # years produced them: a run id that is not an agent run, and a ticket that
    # was deleted without its artifacts.
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.rollback()
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO artifacts (id, ticket_id, run_id, kind, title, content_json,"
                    " evidence_kind, commit_sha, created_at)"
                    " VALUES ('a1', :tid, 'not-an-agent-run', 'log', 'Keep me', '{}', '', '',"
                    " '2026-01-01')"
                ),
                {"tid": ticket_id},
            )
            conn.execute(
                text(
                    "INSERT INTO artifacts (id, ticket_id, run_id, kind, title, content_json,"
                    " evidence_kind, commit_sha, created_at)"
                    " VALUES ('a2', 'deleted-ticket', NULL, 'log', 'Unreachable', '{}', '', '',"
                    " '2026-01-01')"
                )
            )

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.rollback()
        assert len(conn.execute(text("PRAGMA foreign_key_check")).fetchall()) == 2
        conn.rollback()
        with conn.begin():
            m_repair_dangling_references(conn)
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    with Session(engine) as session:
        kept = session.get(Artifact, "a1")
        # Nulled, not deleted: the artifact is a true record of the run's
        # output, and only its pointer was wrong.
        assert kept is not None
        assert kept.run_id is None
        # Dropped: `ticket_id` cannot hold a null, and every read path reaches an
        # artifact through its ticket, so it was unreachable either way.
        assert session.get(Artifact, "a2") is None
