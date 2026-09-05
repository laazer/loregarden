"""Row factories for tests whose subject is not the row itself.

Foreign keys are enforced (``db.session._enforce_foreign_keys``), so a queue
test that wants "a queued run" needs the ticket and the agent run it names to
exist. Writing that out inline is noise in a test about queue ordering, and
getting it wrong fails for a reason that has nothing to do with the assertion.

Each factory commits before returning. That is not incidental: SQLAlchemy orders
a flush by mapper *relationships*, and these models are joined by bare foreign
key columns with no ``Relationship`` between them, so a parent and child added
to one flush can be emitted child-first. Committing the parent first is what
makes the order deterministic.

Ids are accepted rather than generated so a test can keep asserting on the
literal it already reads for ("run-1"), instead of threading a uuid through.
"""

from __future__ import annotations

from loregarden.models.domain import (
    AgentRun,
    OrchestrationRun,
    QueuedRun,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from sqlmodel import Session, select

# Where a workspace points when the test never said. Absolute and deliberately
# nonexistent, because `resolve_workspace_root` resolves a *relative* path
# against `settings.repo_root` — so "." and "" both name the checkout the suite
# is running in. A test that reaches git through one of these workspaces would
# then check branches out in the developer's own tree, which has happened. This
# path fails loudly instead, and names itself in the error.
NO_REPO = "/loregarden-test-workspace-with-no-repo"


def make_workspace(
    session: Session,
    *,
    workspace_id: str | None = None,
    slug: str = "proj",
    repo_path: str = NO_REPO,
) -> Workspace:
    if workspace_id:
        existing_by_id = session.get(Workspace, workspace_id)
        if existing_by_id:
            return existing_by_id
    existing = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if existing:
        return existing
    workspace = Workspace(slug=slug, name=slug, repo_path=repo_path)
    if workspace_id:
        workspace.id = workspace_id
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def make_ticket(
    session: Session,
    *,
    workspace_id: str,
    ticket_id: str | None = None,
    external_id: str | None = None,
    title: str = "Test ticket",
    work_item_type: WorkItemType = WorkItemType.TASK,
    #: Applied only when given, so existing callers keep the model's own default.
    #: Tests about mid-flight behaviour need IN_PROGRESS, and setting it after
    #: the fact is one more line every such test got slightly differently.
    state: TicketState | None = None,
) -> Ticket:
    if ticket_id:
        existing = session.get(Ticket, ticket_id)
        if existing:
            return existing
    ticket = Ticket(
        external_id=external_id or (ticket_id or title),
        workspace_id=workspace_id,
        title=title,
        work_item_type=work_item_type,
    )
    if ticket_id:
        ticket.id = ticket_id
    if state is not None:
        ticket.state = state
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def make_agent_run(
    session: Session,
    *,
    workspace_id: str,
    ticket_id: str | None = None,
    run_id: str | None = None,
    run_code: str = "RUN-1",
    agent_id: str = "backend_implementer",
    orchestration_run_id: str | None = None,
    # Remaining AgentRun fields (status, stderr, stage_key, ...) pass straight
    # through. Naming all of them here would restate the model and go stale with
    # it; SQLModel rejects a name it does not have, so a typo still fails loudly.
    **fields,
) -> AgentRun:
    if run_id:
        existing = session.get(AgentRun, run_id)
        if existing:
            return existing
    if ticket_id:
        make_ticket(session, workspace_id=workspace_id, ticket_id=ticket_id)
    run = AgentRun(
        run_code=run_code,
        ticket_id=ticket_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        orchestration_run_id=orchestration_run_id,
        **fields,
    )
    if run_id:
        run.id = run_id
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def queued_run(
    session: Session,
    *,
    run_id: str,
    ticket_id: str,
    workspace_id: str,
    **fields,
) -> QueuedRun:
    """A ``QueuedRun`` whose ticket, run and workspace exist.

    ``queued_runs`` references all three. Tests that queue work are about
    ordering and promotion, not about the rows on the other end of those
    columns — but the columns still have to resolve, so this creates whatever is
    missing under the ids the test already reads for.
    """
    make_workspace(session, workspace_id=workspace_id, slug=workspace_id)
    make_ticket(session, workspace_id=workspace_id, ticket_id=ticket_id)
    make_agent_run(session, workspace_id=workspace_id, ticket_id=ticket_id, run_id=run_id)
    return QueuedRun(run_id=run_id, ticket_id=ticket_id, workspace_id=workspace_id, **fields)


def make_orchestration_run(
    session: Session,
    *,
    workspace_id: str,
    ticket_id: str,
    orchestration_run_id: str | None = None,
    run_code: str = "ORCH-1",
) -> OrchestrationRun:
    if orchestration_run_id:
        existing = session.get(OrchestrationRun, orchestration_run_id)
        if existing:
            return existing
    run = OrchestrationRun(workspace_id=workspace_id, ticket_id=ticket_id, run_code=run_code)
    if orchestration_run_id:
        run.id = orchestration_run_id
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
