"""Migration 0102 backfills the retry breaker's structural block mark.

`blocked_on_stage_retry_budget` no longer reads blocking prose at all. That
deletion is only safe because the blocks written before the mark existed get
one here: without it, a ticket the breaker blocked would keep its exhausted
counter forever, because re-entering the stage would read the block as somebody
else's.

Applied twice where the point is idempotence — a migration that guards its own
changes is the only kind safe to re-run.
"""

import tempfile

import loregarden.models.domain  # noqa: F401  (registers the tables on SQLModel.metadata)
from loregarden.db import migrations as M
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migrations_retry_block import m_backfill_stage_retry_block
from loregarden.models.domain import (
    Artifact,
    StageBudgetArtifactKind,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

MIGRATION_ID = "0102_backfill_stage_retry_block"


def _engine():
    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/t.db")
    SQLModel.metadata.create_all(engine)
    return engine


def _ticket(
    session: Session,
    workspace: Workspace,
    *,
    state: TicketState,
    blocking_issues: str,
    dispatches: int,
    stage_key: str = "review",
) -> str:
    ticket = Ticket(
        external_id=f"backfill-{blocking_issues[:8]}-{dispatches}-{state.value}",
        workspace_id=workspace.id,
        title="Backfill candidate",
        state=state,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stage_key,
        blocking_issues=blocking_issues,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    for _ in range(dispatches):
        session.add(
            Artifact(
                ticket_id=ticket.id,
                kind=StageBudgetArtifactKind.DISPATCH,
                title=f"stage-dispatch:{stage_key}",
            )
        )
    session.commit()
    return ticket.id


def _marks(session: Session, ticket_id: str, stage_key: str = "review") -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact).where(
                Artifact.ticket_id == ticket_id,
                Artifact.kind == StageBudgetArtifactKind.RETRY_BLOCK,
                Artifact.title == f"stage-retry-block:{stage_key}",
            )
        ).all()
    )


def test_the_backfill_marks_only_the_blocks_the_breaker_stranded():
    engine = _engine()
    with Session(engine) as session:
        workspace = Workspace(slug="backfill-ws", name="Backfill", repo_path="/nonexistent/bf")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)

        stranded_id = _ticket(
            session,
            workspace,
            state=TicketState.BLOCKED,
            blocking_issues=(
                "Stage 'review' reached its retry budget of 5 dispatches "
                "without the workflow advancing past it."
            ),
            dispatches=5,
        )
        # Blocked, says the words, but nothing ever counted a dispatch: there is
        # no exhausted counter for a missing mark to have stranded, so marking
        # it would hand out a reset nobody is owed.
        uncounted_id = _ticket(
            session,
            workspace,
            state=TicketState.BLOCKED,
            blocking_issues="the retry budget is fine; this is a different problem",
            dispatches=0,
        )
        # At budget and blocked, but for something else entirely.
        other_reason_id = _ticket(
            session,
            workspace,
            state=TicketState.BLOCKED,
            blocking_issues="a dependency will not install",
            dispatches=5,
        )
        # The words, the counter — but not blocked.
        running_id = _ticket(
            session,
            workspace,
            state=TicketState.IN_PROGRESS,
            blocking_issues="retry budget",
            dispatches=5,
        )
        # Over-marking, the other direction. Blocked for something unrelated,
        # one dispatch in — nowhere near any budget — but the loose `LIKE
        # '%retry budget%'` matched anyway, and `_MIN_DISPATCHES = 1` let it
        # through. The migration hands this stage a mark it never earned, and
        # `blocked_on_stage_retry_budget` then reads somebody else's block as
        # the breaker's own and grants a free reset. Only the sentence
        # `stage_retry_block_message` actually wrote should match.
        mentions_the_words_id = _ticket(
            session,
            workspace,
            state=TicketState.BLOCKED,
            blocking_issues="the reviewer wants the retry budget documented before this ships",
            dispatches=1,
        )

    with engine.begin() as conn:
        m_backfill_stage_retry_block(conn)

    with Session(engine) as session:
        assert len(_marks(session, stranded_id)) == 1
        assert _marks(session, uncounted_id) == []
        assert _marks(session, other_reason_id) == []
        assert _marks(session, running_id) == []
        assert _marks(session, mentions_the_words_id) == []

    # Re-running is what happens when a branch that already migrated is merged
    # forward: the mark must not double.
    with engine.begin() as conn:
        m_backfill_stage_retry_block(conn)
    with Session(engine) as session:
        assert len(_marks(session, stranded_id)) == 1


def test_the_backfill_is_registered_after_the_migration_it_unblocks():
    assert MIGRATION_ID in SHIPPED_MIGRATION_IDS
    assert [migration_id for migration_id, _ in M.MIGRATIONS][-1] == MIGRATION_ID
