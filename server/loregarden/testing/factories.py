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

import json

from sqlmodel import Session, select

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ExitActionRequirementKind,
    ExitActionResolution,
    OperatorJudgmentRequirement,
    OrchestrationRun,
    QueuedRun,
    Ticket,
    TicketState,
    WorkflowExitAction,
    WorkflowStageDef,
    WorkItemType,
    Workspace,
)
from loregarden.services.exit_actions import resolve_exit_actions

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
    parent_ticket_id: str | None = None,
    description: str = "",
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
        parent_ticket_id=parent_ticket_id,
        description=description,
    )
    if ticket_id:
        ticket.id = ticket_id
    if state is not None:
        ticket.state = state
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def make_workspace_ticket(
    session: Session,
    external_id: str,
    *,
    slug: str = "loregarden",
    state: TicketState = TicketState.IN_PROGRESS,
) -> Ticket:
    """A ticket in an already-seeded workspace, looked up by slug.

    Three suites had grown the same five-line wrapper around `make_ticket` —
    resolve the workspace, pass IN_PROGRESS, title it after its own id — which is
    what the DRY gate flagged. The workspace lookup is the part worth sharing:
    the tests that need this are about runs, events and prompts, and none of them
    care which workspace their ticket is in beyond it existing.
    """
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
    return make_ticket(
        session,
        workspace_id=workspace.id,
        external_id=external_id,
        title=external_id,
        state=state,
    )


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


def make_artifact(
    session: Session,
    *,
    ticket_id: str,
    kind: str,  # py-org: allow-string - artifact kinds are agent-supplied and open-ended (25+ live, no enum)
    title: str,
    content: dict,
    artifact_id: str | None = None,
) -> Artifact:
    """An artifact on an existing ticket, its content serialised the way the
    attach tools store it."""
    if artifact_id:
        existing = session.get(Artifact, artifact_id)
        if existing:
            return existing
    artifact = Artifact(
        ticket_id=ticket_id, kind=kind, title=title, content_json=json.dumps(content)
    )
    if artifact_id:
        artifact.id = artifact_id
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def operator_judgment_gate_payload(stage_name: str = "stage") -> str:
    """The exit-action payload of a sign-off gate, as the resolver writes it.

    One operator-judgment action — the shape migrations 0138/0149 give every
    human gate — resolved by the real resolver, so a test approving "a gate"
    approves one the server would accept. A blank payload is refused: approving
    it would attest nothing.
    """
    stage = WorkflowStageDef(
        key="gate",
        name=stage_name,
        exit_actions_enabled=True,
        exit_actions=[
            WorkflowExitAction(
                key="legacy-stage-sign-off",
                label=f"Approve {stage_name} completion",
                requirement=OperatorJudgmentRequirement(
                    kind=ExitActionRequirementKind.OPERATOR_JUDGMENT,
                    decision_prompt=f"Approve completion of stage '{stage_name}'.",
                ),
            )
        ],
    )
    resolution = resolve_exit_actions(stage, None)
    return ExitActionResolution(
        human_required_actions=resolution.human_required_actions,
        allowed_actions=resolution.allowed_actions,
    ).model_dump_json()
