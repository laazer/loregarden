"""Two-way sync between tickets and GitHub issues.

**What syncs.** Three fields: the title, the body (the ticket's description),
and the closure — open, closed as completed (``done``), or closed as not
planned (``wont_do``). Nothing else crosses: acceptance criteria, stages and
priority have no issue-side home, and comments stay where they were written.

**How a direction is chosen.** Each link stores the content both sides agreed on
at the last sync. Per field, compared with that base:

- only the ticket changed → push it to the issue;
- only the issue changed → pull it into the ticket;
- both changed to the same value → nothing to do but advance the base;
- both changed differently → a conflict, settled by `ConflictPolicy`. The
  default reports it and leaves both sides as they are, and the base stays put
  so the conflict keeps being reported until someone resolves it.

Pulls go through `update_ticket_manual`, the same door the board and
`loregarden_update_ticket` use, so a closure pulled from GitHub settles the
ticket's orchestration exactly as an operator's click would.

**Failure.** A `gh` failure on one link is recorded on the link (`last_error`)
and returned on its result; a workspace sync carries on with the next link
rather than letting one deleted issue hide the rest. The base only advances for
a field that actually reached both sides, so a partial failure heals on the
next sync instead of being mistaken for agreement.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from loregarden.models.domain import (
    ConflictPolicy,
    GithubIssueLink,
    IssueClosure,
    IssueSnapshot,
    LinkSyncResult,
    SyncConflict,
    SyncField,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkItemType,
    Workspace,
    WorkspaceSyncResult,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.github_issue_client import (
    GithubIssue,
    GithubIssueError,
    create_issue,
    edit_issue,
    get_issue,
    list_open_issues,
    resolve_repo,
    set_issue_closure,
)
from loregarden.services.github_sync_origin import applying_remote_changes
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_service import TicketService
from loregarden.services.ticket_state_service import can_choose
from loregarden.services.ticket_tags import serialize_tags
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Who a pulled edit is attributed to on the ticket.
SYNC_ACTOR = "github"

_STATE_FOR_CLOSURE: dict[IssueClosure, TicketState] = {
    IssueClosure.OPEN: TicketState.BACKLOG,
    IssueClosure.COMPLETED: TicketState.DONE,
    IssueClosure.NOT_PLANNED: TicketState.WONT_DO,
}


class FieldOutcome(StrEnum):
    UNCHANGED = "unchanged"
    PUSH = "push"
    PULL = "pull"
    CONVERGED = "converged"
    CONFLICT = "conflict"


def canonical_text(value: str | None) -> str:
    """Line endings and surrounding whitespace, the way both sides store them.

    GitHub's web editor saves CRLF and ticket creation strips, so without this a
    body nobody touched would read as edited on both sides after its first trip.
    """
    return (value or "").replace("\r\n", "\n").strip()


def closure_for_state(state: TicketState) -> IssueClosure:
    if state is TicketState.DONE:
        return IssueClosure.COMPLETED
    if state is TicketState.WONT_DO:
        return IssueClosure.NOT_PLANNED
    return IssueClosure.OPEN


def ticket_snapshot(ticket: Ticket) -> IssueSnapshot:
    return IssueSnapshot(
        title=canonical_text(ticket.title),
        body=canonical_text(ticket.description),
        closure=closure_for_state(ticket.state),
    )


def issue_snapshot(issue: GithubIssue) -> IssueSnapshot:
    return IssueSnapshot(
        title=canonical_text(issue.title),
        body=canonical_text(issue.body),
        closure=issue.closure,
    )


def base_snapshot(link: GithubIssueLink) -> IssueSnapshot:
    return IssueSnapshot(
        title=link.synced_title, body=link.synced_body, closure=link.synced_closure
    )


def merge_field(base: str, local: str, remote: str) -> FieldOutcome:
    """The three-way decision for one field. Pure."""
    if local == remote:
        return FieldOutcome.UNCHANGED if local == base else FieldOutcome.CONVERGED
    if remote == base:
        return FieldOutcome.PUSH
    if local == base:
        return FieldOutcome.PULL
    return FieldOutcome.CONFLICT


def _resolve_conflict(outcome: FieldOutcome, policy: ConflictPolicy) -> FieldOutcome:
    if outcome is not FieldOutcome.CONFLICT:
        return outcome
    if policy is ConflictPolicy.LOCAL:
        return FieldOutcome.PUSH
    if policy is ConflictPolicy.REMOTE:
        return FieldOutcome.PULL
    return FieldOutcome.CONFLICT


def plan_sync(
    base: IssueSnapshot,
    local: IssueSnapshot,
    remote: IssueSnapshot,
    policy: ConflictPolicy = ConflictPolicy.REPORT,
) -> dict[SyncField, FieldOutcome]:
    """Per-field outcome of merging `local` and `remote` against `base`. Pure."""
    values = {
        SyncField.TITLE: (base.title, local.title, remote.title),
        SyncField.BODY: (base.body, local.body, remote.body),
        SyncField.CLOSURE: (base.closure.value, local.closure.value, remote.closure.value),
    }
    return {
        field: _resolve_conflict(merge_field(*triple), policy) for field, triple in values.items()
    }


def _field_value(snapshot: IssueSnapshot, field: SyncField) -> str:
    return snapshot.model_dump(mode="json")[field.value]


def _record_base(link: GithubIssueLink, snapshot: IssueSnapshot, fields: set[SyncField]) -> None:
    if SyncField.TITLE in fields:
        link.synced_title = snapshot.title
    if SyncField.BODY in fields:
        link.synced_body = snapshot.body
    if SyncField.CLOSURE in fields:
        link.synced_closure = snapshot.closure


def _pull(session: Session, ticket: Ticket, remote: IssueSnapshot, fields: set[SyncField]) -> None:
    """Write the issue's side of `fields` into the ticket, through the edit path."""
    orch = OrchestrationService(session)
    target_state: TicketState | None = None
    if SyncField.CLOSURE in fields:
        target_state = _STATE_FOR_CLOSURE[remote.closure]
        # Reopening lands in the backlog, but a ticket that is open already
        # (it cannot be here unless the base was closed) keeps its state.
        if remote.closure is IssueClosure.OPEN and ticket.state not in (
            TicketState.DONE,
            TicketState.WONT_DO,
        ):
            target_state = None
        # wont_do -> done is not a move the state machine allows directly; a
        # finished-or-abandoned ticket reopens through the backlog.
        elif target_state is not None and not can_choose(ticket.state, target_state):
            orch.update_ticket_manual(ticket, UpdateTicketRequest(state=TicketState.BACKLOG))
    request = UpdateTicketRequest(
        title=remote.title if SyncField.TITLE in fields else None,
        description=remote.body if SyncField.BODY in fields else None,
        state=target_state,
    )
    orch.update_ticket_manual(ticket, request)
    ticket.last_updated_by = SYNC_ACTOR
    session.add(ticket)
    session.commit()


def _push(
    link: GithubIssueLink,
    local: IssueSnapshot,
    remote: IssueSnapshot,
    fields: set[SyncField],
    *,
    workspace: Workspace,
) -> None:
    cwd = resolve_workspace_root(workspace)
    if SyncField.TITLE in fields or SyncField.BODY in fields:
        edit_issue(
            link.repo,
            link.issue_number,
            cwd=cwd,
            title=local.title if SyncField.TITLE in fields else None,
            body=local.body if SyncField.BODY in fields else None,
        )
    if SyncField.CLOSURE in fields:
        # Changing *why* a closed issue closed means reopening it first: `gh
        # issue close` on a closed issue leaves its reason as it was.
        if remote.closure is not IssueClosure.OPEN and local.closure is not IssueClosure.OPEN:
            set_issue_closure(link.repo, link.issue_number, IssueClosure.OPEN, cwd=cwd)
        set_issue_closure(link.repo, link.issue_number, local.closure, cwd=cwd)


def _result(link: GithubIssueLink, ticket: Ticket) -> LinkSyncResult:
    return LinkSyncResult(
        ticket_id=ticket.id,
        external_id=ticket.external_id,
        issue_number=link.issue_number,
        issue_url=link.issue_url,
    )


def _fail(
    session: Session, link: GithubIssueLink, result: LinkSyncResult, exc: Exception
) -> LinkSyncResult:
    logger.warning(
        "GitHub issue sync failed for %s#%s (ticket %s): %s",
        link.repo,
        link.issue_number,
        result.external_id,
        exc,
    )
    link.last_error = str(exc)
    session.add(link)
    session.commit()
    result.error = str(exc)
    return result


def sync_link(
    session: Session,
    link: GithubIssueLink,
    *,
    issue: GithubIssue | None = None,
    policy: ConflictPolicy = ConflictPolicy.REPORT,
) -> LinkSyncResult:
    """Sync one linked ticket with its issue.

    `issue` lets a webhook hand over the issue it was sent rather than fetching
    it again. Never raises for a `gh` failure — see the module docstring.
    """
    ticket = session.get(Ticket, link.ticket_id)
    workspace = session.get(Workspace, link.workspace_id)
    if ticket is None or workspace is None:
        raise LookupError(f"GitHub issue link {link.id} points at a missing ticket or workspace")
    result = _result(link, ticket)
    try:
        if issue is None:
            issue = get_issue(link.repo, link.issue_number, cwd=resolve_workspace_root(workspace))
    except GithubIssueError as exc:
        return _fail(session, link, result, exc)

    base = base_snapshot(link)
    local = ticket_snapshot(ticket)
    remote = issue_snapshot(issue)
    plan = plan_sync(base, local, remote, policy)

    pulls = {f for f, o in plan.items() if o is FieldOutcome.PULL}
    pushes = {f for f, o in plan.items() if o is FieldOutcome.PUSH}
    converged = {f for f, o in plan.items() if o is FieldOutcome.CONVERGED}
    result.conflicts = [
        SyncConflict(
            field=f,
            base=_field_value(base, f),
            local=_field_value(local, f),
            remote=_field_value(remote, f),
        )
        for f, o in plan.items()
        if o is FieldOutcome.CONFLICT
    ]

    if pulls:
        with applying_remote_changes():
            _pull(session, ticket, remote, pulls)
        result.pulled = sorted(pulls)
    _record_base(link, remote, pulls | converged)

    try:
        if pushes:
            _push(link, local, remote, pushes, workspace=workspace)
            result.pushed = sorted(pushes)
    except GithubIssueError as exc:
        return _fail(session, link, result, exc)
    _record_base(link, local, pushes)

    if issue.web_url:
        link.issue_url = issue.web_url
    link.last_error = ""
    link.last_synced_at = utcnow()
    session.add(link)
    session.commit()
    return result


def link_for_ticket(session: Session, ticket_id: str) -> GithubIssueLink | None:
    return session.exec(
        select(GithubIssueLink).where(GithubIssueLink.ticket_id == ticket_id)
    ).first()


def publish_ticket(session: Session, ticket: Ticket) -> LinkSyncResult:
    """Open a GitHub issue for a ticket and link the two. Raises if it cannot."""
    if link_for_ticket(session, ticket.id) is not None:
        raise ValueError(f"{ticket.external_id} is already linked to a GitHub issue")
    if ticket.workspace_id is None:
        raise ValueError("Initiatives span workspaces and have no repository to open an issue in")
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        raise LookupError("Workspace not found")
    cwd = resolve_workspace_root(workspace)
    local = ticket_snapshot(ticket)
    repo = resolve_repo(cwd)
    number, url = create_issue(repo, title=local.title, body=local.body, cwd=cwd)
    link = GithubIssueLink(
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        repo=repo,
        issue_number=number,
        issue_url=url,
        synced_title=local.title,
        synced_body=local.body,
        synced_closure=IssueClosure.OPEN,
    )
    session.add(link)
    session.commit()
    result = _result(link, ticket)
    if local.closure is not IssueClosure.OPEN:
        # The issue opened; the ticket is already closed. The next sync pushes it.
        return sync_link(session, link)
    return result


def import_issue(
    session: Session,
    workspace: Workspace,
    repo: str,
    issue: GithubIssue,
    *,
    parent_ticket_id: str,
    work_item_type: WorkItemType = WorkItemType.BUG,
) -> LinkSyncResult:
    """Create a ticket for an issue and link them. Raises if the ticket cannot be made."""
    remote = issue_snapshot(issue)
    ticket = TicketService(session).create_ticket(
        workspace_slug=workspace.slug,
        title=remote.title,
        work_item_type=work_item_type,
        parent_ticket_id=parent_ticket_id,
        description=remote.body,
    )
    labels = [label.name for label in issue.labels]
    if labels:
        ticket.tags_json = serialize_tags(labels)
        session.add(ticket)
    link = GithubIssueLink(
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        repo=repo,
        issue_number=issue.number,
        issue_url=issue.web_url,
        synced_title=remote.title,
        synced_body=remote.body,
        synced_closure=remote.closure,
    )
    session.add(link)
    session.commit()
    return _result(link, ticket)


def _linked_numbers(session: Session, workspace_id: str, repo: str) -> set[int]:
    return set(
        session.exec(
            select(GithubIssueLink.issue_number).where(
                GithubIssueLink.workspace_id == workspace_id,
                GithubIssueLink.repo == repo,
            )
        ).all()
    )


def sync_workspace(
    session: Session,
    workspace: Workspace,
    *,
    import_parent_ticket_id: str = "",
    import_work_item_type: WorkItemType = WorkItemType.BUG,
    import_label: str = "",
    policy: ConflictPolicy = ConflictPolicy.REPORT,
) -> WorkspaceSyncResult:
    """Sync every linked ticket in the workspace; optionally import unlinked open issues.

    Importing needs a parent: every work item below a milestone hangs off one,
    so the caller names where new issues land. Without it, nothing is imported.
    Raises only if the repository itself cannot be resolved or listed.
    """
    cwd = resolve_workspace_root(workspace)
    repo = resolve_repo(cwd)
    outcome = WorkspaceSyncResult(workspace_slug=workspace.slug, repo=repo)
    links = session.exec(
        select(GithubIssueLink).where(GithubIssueLink.workspace_id == workspace.id)
    ).all()
    outcome.links = [sync_link(session, link, policy=policy) for link in links]

    if not import_parent_ticket_id:
        return outcome
    linked = _linked_numbers(session, workspace.id, repo)
    for issue in list_open_issues(repo, cwd=cwd, label=import_label):
        if issue.number in linked:
            continue
        outcome.imported.append(
            import_issue(
                session,
                workspace,
                repo,
                issue,
                parent_ticket_id=import_parent_ticket_id,
                work_item_type=import_work_item_type,
            )
        )
    return outcome


def unlink_ticket(session: Session, ticket_id: str) -> bool:
    """Drop the link; the ticket and the issue both stay. False if there was none."""
    link = link_for_ticket(session, ticket_id)
    if link is None:
        return False
    session.delete(link)
    session.commit()
    return True


class GithubRepositoryRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str


class IssueWebhookEvent(BaseModel):
    """The part of a GitHub ``issues`` webhook delivery the sync reads."""

    model_config = ConfigDict(extra="ignore")

    action: str
    issue: GithubIssue
    repository: GithubRepositoryRef


#: Deliveries after which the issue is gone from this repository. The link is
#: dropped; the ticket stays.
_ISSUE_GONE_ACTIONS = frozenset({"deleted", "transferred"})  # py-org: allow-string — GitHub's


def handle_issue_event(
    session: Session,
    workspace: Workspace,
    event: IssueWebhookEvent,
    *,
    import_parent_ticket_id: str = "",
) -> LinkSyncResult | None:
    """Apply one ``issues`` delivery. None when it concerns no ticket here.

    A linked issue is synced against the payload's copy of it. An unlinked one
    that was just opened is imported, but only when the webhook was configured
    with a parent to import under — otherwise it is not ours.
    """
    repo = event.repository.full_name
    link = session.exec(
        select(GithubIssueLink).where(
            GithubIssueLink.workspace_id == workspace.id,
            GithubIssueLink.repo == repo,
            GithubIssueLink.issue_number == event.issue.number,
        )
    ).first()
    if link is not None:
        if event.action in _ISSUE_GONE_ACTIONS:
            logger.warning(
                "GitHub issue %s#%s was %s; unlinking ticket %s",
                repo,
                event.issue.number,
                event.action,
                link.ticket_id,
            )
            session.delete(link)
            session.commit()
            return None
        return sync_link(session, link, issue=event.issue)
    # py-org: allow-string — GitHub's webhook action vocabulary.
    if event.action == "opened" and import_parent_ticket_id:
        return import_issue(
            session, workspace, repo, event.issue, parent_ticket_id=import_parent_ticket_id
        )
    return None
