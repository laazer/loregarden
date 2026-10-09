"""Migration 20261008_handoff_notice_kind moves unchecked-handoff notices off `error`.

Only those rows: a real error on the same ticket, and a notice-like title on a
non-error kind, must stay where they are. Applied twice for idempotence.
"""

import tempfile

import loregarden.models.domain  # noqa: F401  (registers the tables on SQLModel.metadata)
from loregarden.db.versions.handoff_notice_kind import m_handoff_notice_kind
from loregarden.models.domain import Artifact, ArtifactKind, Ticket, WorkItemType, Workspace
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select


def test_moves_only_handoff_notices_and_is_idempotent():
    engine = create_engine(f"sqlite:///{tempfile.mkdtemp()}/t.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        workspace = Workspace(slug="w", name="w", repo_path="/nonexistent")
        session.add(workspace)
        session.commit()
        ticket = Ticket(
            external_id="t-1",
            workspace_id=workspace.id,
            title="t",
            work_item_type=WorkItemType.TASK,
        )
        session.add(ticket)
        session.commit()
        rows = {
            "notice": Artifact(
                ticket_id=ticket.id, kind="error", title="Handoff not validated — a → b"
            ),
            "real_error": Artifact(
                ticket_id=ticket.id, kind="error", title="Transition gate failed — implementation"
            ),
            "other_kind": Artifact(
                ticket_id=ticket.id, kind="context", title="Handoff not validated — quoted"
            ),
        }
        session.add_all(rows.values())
        session.commit()
        ids = {name: row.id for name, row in rows.items()}

    for _ in range(2):
        with engine.begin() as conn:
            m_handoff_notice_kind(conn)

    with Session(engine) as session:
        kinds = {
            name: session.exec(select(Artifact.kind).where(Artifact.id == row_id)).one()
            for name, row_id in ids.items()
        }
    assert kinds == {
        "notice": ArtifactKind.HANDOFF_NOT_VALIDATED,
        "real_error": ArtifactKind.ERROR,
        "other_kind": "context",
    }
