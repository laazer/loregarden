"""Migration 0103 moves the rework-feedback ledger onto its own artifact kind.

The backfill is load-bearing, not cosmetic. Both ledger readers filter on kind
*and* title, so a row left on the old `context` kind is invisible to them — and
the row count for a target stage is precisely the loop metric
`MAX_REWORK_REROUTES` caps. An unmigrated ticket would come back from the
migration with its reroute count silently reset to zero and its cap disarmed,
which is why the count is asserted through the reader rather than over the
table.

Applied twice where the point is idempotence — a migration that guards its own
changes is the only kind safe to re-run.
"""

import tempfile

import loregarden.models.domain  # noqa: F401  (registers the tables on SQLModel.metadata)
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migrations import MIGRATIONS
from loregarden.db.migrations_rework_kind import m_rework_feedback_kind
from loregarden.models.domain import (
    Artifact,
    ReworkArtifactKind,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.rework_feedback import (
    record_rework_feedback,
    render_rework_feedback,
    rework_reroute_count,
)
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

MIGRATION_ID = "0103_rework_feedback_kind"

#: The title the ledger wrote before and after the move — unchanged by it, and
#: the only thing that distinguishes its rows inside the old shared bucket.
LEDGER_TITLE = "Rework feedback — implement"


def _engine():
    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/t.db")
    SQLModel.metadata.create_all(engine)
    return engine


def _ticket(session: Session, workspace: Workspace, *, external_id: str) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Ledger owner",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _legacy_row(session: Session, ticket: Ticket, *, title: str, kind: str, content: str) -> None:
    """A row exactly as it was written before the ledger had its own kind."""
    session.add(Artifact(ticket_id=ticket.id, kind=kind, title=title, content_json=content))
    session.commit()


def _kinds(session: Session, ticket_id: str) -> list[tuple[str, str]]:
    rows = session.exec(select(Artifact).where(Artifact.ticket_id == ticket_id)).all()
    return sorted((a.title, a.kind) for a in rows)


def test_the_migration_is_registered_under_the_id_it_ships_as():
    assert MIGRATION_ID in SHIPPED_MIGRATION_IDS
    assert MIGRATION_ID in [migration_id for migration_id, _ in MIGRATIONS]


def test_the_backfill_restores_the_reroute_count_the_readers_would_have_lost():
    """The whole point: an already-recorded loop must still be countable."""
    engine = _engine()
    with Session(engine) as session:
        workspace = Workspace(slug="rk-ws", name="Rework", repo_path="/nonexistent/rk")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        ticket = _ticket(session, workspace, external_id="rk-count")

        for round_number in range(3):
            _legacy_row(
                session,
                ticket,
                title=LEDGER_TITLE,
                kind="context",
                content=(
                    '{"from_stage": "verify", "target_stage": "implement", '
                    f'"context": "round {round_number}"}}'
                ),
            )

        # The state this migration exists to prevent: three real reroutes, and
        # the reader that caps the loop sees none of them.
        assert rework_reroute_count(session, ticket, "implement") == 0

        m_rework_feedback_kind(session.connection())
        session.commit()

        assert rework_reroute_count(session, ticket, "implement") == 3
        rendered = render_rework_feedback(session, ticket, "implement")
        assert "round 0" in rendered and "round 2" in rendered


def test_the_backfill_moves_only_the_ledger_and_is_idempotent():
    engine = _engine()
    with Session(engine) as session:
        workspace = Workspace(slug="rk-ws2", name="Rework", repo_path="/nonexistent/rk2")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        ticket = _ticket(session, workspace, external_id="rk-precision")

        _legacy_row(session, ticket, title=LEDGER_TITLE, kind="context", content="{}")
        # Ordinary run context, sharing the bucket the ledger is leaving.
        _legacy_row(session, ticket, title="Run context", kind="context", content="{}")
        # The row that makes the title *prefix* load-bearing rather than
        # incidental: same kind as the ledger, and it matches the loose
        # `'%ework%'` pattern the ticket's own exploratory query used, while
        # falling outside the ledger's own title prefix — so only
        # anchoring on the ledger's real title leaves it alone. Without this
        # row the kind guard alone would carry the test, and a predicate
        # broadened to `'%ework%'` would pass it.
        _legacy_row(
            session, ticket, title="Rework notes — verify context", kind="context", content="{}"
        )
        # Rework in the title, but never a ledger row — these kinds stay put.
        _legacy_row(
            session, ticket, title="Rework verified: 56 passing", kind="evidence", content="{}"
        )
        _legacy_row(
            session, ticket, title="Triage rework — scope unchanged", kind="log", content="{}"
        )

        m_rework_feedback_kind(session.connection())
        session.commit()
        after_once = _kinds(session, ticket.id)

        assert after_once == [
            (LEDGER_TITLE, ReworkArtifactKind.FEEDBACK.value),
            ("Rework notes — verify context", "context"),
            ("Rework verified: 56 passing", "evidence"),
            ("Run context", "context"),
            ("Triage rework — scope unchanged", "log"),
        ]

        m_rework_feedback_kind(session.connection())
        session.commit()
        assert _kinds(session, ticket.id) == after_once


def test_new_reroutes_are_written_on_the_dedicated_kind():
    """Forward half of the change: nothing new lands in the shared bucket."""
    engine = _engine()
    with Session(engine) as session:
        workspace = Workspace(slug="rk-ws3", name="Rework", repo_path="/nonexistent/rk3")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        ticket = _ticket(session, workspace, external_id="rk-forward")

        record_rework_feedback(
            session, ticket, target_stage="implement", from_stage="verify", context="fix the thing"
        )

        assert _kinds(session, ticket.id) == [(LEDGER_TITLE, ReworkArtifactKind.FEEDBACK.value)]
        assert rework_reroute_count(session, ticket, "implement") == 1
