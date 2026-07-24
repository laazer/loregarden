from loregarden.db.enum_integrity import (
    _enum_columns,
    find_unreadable_enum_values,
    report_unreadable_enum_values,
)
from loregarden.models.domain import Ticket
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine


def _engine_with_a_ticket(tmp_path, name: str):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Ticket(id="t1", external_id="ext-1", workspace_id="ws1", title="A ticket"))
        session.commit()
    return engine


def test_the_scan_actually_covers_the_enum_columns():
    """A green check that inspected nothing is worse than no check.

    ``SQLModel.metadata`` is only populated as a side effect of importing the model
    modules, so without an explicit import the scan quietly found zero columns and
    every other test here still passed.
    """
    covered = {(table, column) for table, column, _, _ in _enum_columns()}
    assert ("tickets", "state") in covered
    assert ("agent_runs", "status") in covered
    assert ("approvals", "status") in covered
    assert ("domain_events", "type") in covered


def test_a_clean_database_reports_nothing(tmp_path):
    engine = _engine_with_a_ticket(tmp_path, "clean.db")
    assert report_unreadable_enum_values(engine) == []


def test_an_unreadable_value_is_named_precisely(tmp_path):
    """The check exists to replace a fifteen-frame LookupError with an actionable line."""
    engine = _engine_with_a_ticket(tmp_path, "dirty.db")
    with engine.begin() as conn:
        conn.execute(text("UPDATE tickets SET state = 'BLOCKED' WHERE id = 't1'"))

    with engine.connect() as conn:
        issues = find_unreadable_enum_values(conn)

    assert len(issues) == 1
    issue = issues[0]
    assert (issue.table, issue.column, issue.value) == ("tickets", "state", "BLOCKED")
    assert issue.row_ids == ["t1"]
    described = issue.describe()
    assert "tickets.state" in described
    assert "t1" in described
    assert "blocked" in described  # lists the values it should have been


def test_the_check_survives_a_table_the_schema_has_not_created(tmp_path):
    """Runs before seeding on a fresh database, so missing tables must not raise."""
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert report_unreadable_enum_values(engine) == []
