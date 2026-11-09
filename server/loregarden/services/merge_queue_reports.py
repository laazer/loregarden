"""Where a merge queue entry that stopped is reported, so it is never only logged.

An entry that fails, times out or is refused leaves the queue — the candidate
behind it must not wait on it — and its reason goes on the ticket as an inbox
card naming the PR, the reason and the command that retries it. The card
closes when the entry merges or is retried.

A conflict only a person can resolve also gets a durable artifact: a bug
ticket naming the PR, the base, the conflicted paths and git's words, whose
external id the entry records (`artifact_ref`). While that ticket is open
every cycle reports the blocked state again — the first report at warning,
with the card, and every later one at info naming the artifact — so the
alert neither repeats at full severity forever nor goes silent. The entry
retries itself once that ticket is done (`merge_queue_loop`).
"""

from __future__ import annotations

import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    VALID_HIERARCHY,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    HumanActionTier,
    MergeQueueEntry,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
    utcnow,
)
from loregarden.services.prepared_action import PreparedAction
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Every queue card's title starts with this.
QUEUE_CARD_PREFIX = "Merge queue stopped: "
RESOLVED_BY_QUEUE = "merge_queue"


def queue_card_title(entry: MergeQueueEntry) -> str:
    return f"{QUEUE_CARD_PREFIX}PR #{entry.pr_number}"


def retry_command(entry: MergeQueueEntry) -> str:
    return (
        "./scripts/loregarden-cli.sh mcp call loregarden_dequeue_merge "
        f"entry_id={entry.id} action=retry"
    )


def _pending(session: Session, entry: MergeQueueEntry) -> list[Approval]:
    return list(
        session.exec(
            select(Approval).where(
                # By workspace, not ticket: a PR admitted by number has none,
                # and `ticket_id == NULL` matches no row. The title names the PR.
                Approval.workspace_id == entry.workspace_id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == queue_card_title(entry),
            )
        ).all()
    )


def report_stopped(session: Session, entry: MergeQueueEntry) -> Approval:
    """File the card for an entry that left the queue unmerged, or refresh the open one."""
    impact = (
        f"PR #{entry.pr_number} ({entry.pr_url or 'no URL'}) left the merge queue "
        f"{entry.state.value}: {entry.detail}"
    )
    if entry.artifact_ref:
        impact += (
            f"\n\nResolve it through {entry.artifact_ref}; the entry retries once that is done."
        )
    if entry.log_tail:
        impact += f"\n\nFailing log tail:\n\n{entry.log_tail[-4000:]}"
    action = PreparedAction(
        tier=HumanActionTier.MANUAL,
        attempted=f"Landed PR #{entry.pr_number} through the merge queue.",
        command=retry_command(entry),
    )
    logger.warning(
        "Merge queue: PR #%s left the queue %s: %s",
        entry.pr_number,
        entry.state.value,
        entry.detail,
    )
    existing = _pending(session, entry)
    if existing:
        card = existing[0]
        if card.impact != impact:
            card.impact = impact
            card.tool_input_json = action.model_dump_json()
            session.add(card)
            session.commit()
        return card
    card = Approval(
        ticket_id=entry.ticket_id,
        workspace_id=entry.workspace_id,
        kind=ApprovalKind.HUMAN_ACTION,
        title=queue_card_title(entry),
        level="high",
        impact=impact,
        tool_name=action.command,
        tool_input_json=action.model_dump_json(),
        status=ApprovalStatus.PENDING,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    event_bus.publish(
        session,
        EventType.APPROVAL_REQUESTED,
        workspace_id=entry.workspace_id,
        ticket_id=entry.ticket_id,
        payload={"approval_id": card.id, "merge_queue_entry": entry.id},
    )
    return card


def close_card(session: Session, entry: MergeQueueEntry, *, reason: str) -> int:
    cards = _pending(session, entry)
    for card in cards:
        card.status = ApprovalStatus.APPROVED
        card.resolved_by = RESOLVED_BY_QUEUE
        card.resolved_at = utcnow()
        session.add(card)
    if not cards:
        return 0
    session.commit()
    for card in cards:
        event_bus.publish(
            session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=entry.workspace_id,
            ticket_id=entry.ticket_id,
            payload={"approval_id": card.id, "reason": reason},
        )
    return len(cards)


def file_conflict_artifact(
    session: Session, entry: MergeQueueEntry, ticket: Ticket
) -> Ticket | None:
    """The bug ticket the staging conflict is resolved through; None when none can hold it.

    Filed under the nearest of ``ticket`` and its ancestors that may contain a
    bug — the tree's root, for a tree — because that is where the fix lands:
    the root's children land on its integration branch, which is the branch
    that must take the base. It also reopens a finished root, which is honest:
    the tree has not reached the base. A ticket with no such ancestor gets no
    artifact, and the card filed for the entry is the record.
    """
    parent = _bug_holder(session, ticket)
    if parent is None:
        logger.warning(
            "Merge queue: no ticket above %s can hold a conflict bug; PR #%s's card is the record",
            ticket.external_id,
            entry.pr_number,
        )
        return None
    workspace = session.get(Workspace, entry.workspace_id)
    workspace_slug = workspace.slug if workspace else None
    try:
        return _create_artifact(session, entry, ticket, parent, workspace_slug)
    except ValueError:
        # The card is the alternate record and carries the same paths and
        # git's words; the warning says why there is no ticket beside it.
        logger.exception(
            "Merge queue: could not file a conflict ticket for PR #%s under %s",
            entry.pr_number,
            parent.external_id,
        )
        session.rollback()
        return None


def _create_artifact(
    session: Session,
    entry: MergeQueueEntry,
    ticket: Ticket,
    parent: Ticket,
    workspace_slug: str | None,
) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=workspace_slug,
        title=f"Resolve {entry.source_branch or 'the tree'}'s conflict with {entry.base_branch} "
        f"(PR #{entry.pr_number})",
        work_item_type=WorkItemType.BUG,
        parent_ticket_id=parent.id,
        description="\n".join(
            [
                f"The merge queue could not stage PR #{entry.pr_number} ({entry.pr_url}) for "
                f"{ticket.external_id}: merging {entry.head_sha[:12]} onto "
                f"{entry.base_branch} conflicts.",
                "",
                "Conflicted paths:",
                *[f"- `{path}`" for path in entry.conflicted_files()],
                "",
                "git said:",
                "",
                "```",
                entry.detail[-3000:],
                "```",
                "",
                f"Resolve it on `{entry.source_branch}` (merge `{entry.base_branch}` into it), "
                "then mark this done; the queue entry retries on its own. A new landing on the "
                "branch republishes and re-queues it as well.",
            ]
        ),
        acceptance_criteria=[
            f"{entry.source_branch} merges {entry.base_branch} cleanly",
            f"PR #{entry.pr_number} lands through the merge queue",
        ],
        priority=1,
        created_by=RESOLVED_BY_QUEUE,
    )


def _bug_holder(session: Session, ticket: Ticket) -> Ticket | None:
    current: Ticket | None = ticket
    while current is not None:
        if WorkItemType.BUG in VALID_HIERARCHY.get(current.work_item_type, []):
            return current
        current = (
            session.get(Ticket, current.parent_ticket_id) if current.parent_ticket_id else None
        )
    return None


def artifact_open(session: Session, entry: MergeQueueEntry) -> bool | None:
    """Whether the entry's conflict ticket is still open; None when it names none."""
    if not entry.artifact_ref:
        return None
    ticket = session.exec(
        select(Ticket).where(
            Ticket.workspace_id == entry.workspace_id, Ticket.external_id == entry.artifact_ref
        )
    ).first()
    if ticket is None:
        # Deleted by hand: nothing remains to resolve through, so it is not
        # open — and the retry that follows re-surfaces the conflict if it
        # still stands.
        logger.warning(
            "Merge queue: PR #%s's conflict ticket %s no longer exists",
            entry.pr_number,
            entry.artifact_ref,
        )
        return False
    return ticket.state not in (TicketState.DONE, TicketState.WONT_DO)


def report_still_blocked(entry: MergeQueueEntry) -> None:
    """The reminder every later cycle gives: info, naming the artifact."""
    logger.info(
        "Merge queue: PR #%s still blocked on conflict ticket %s (%s)",
        entry.pr_number,
        entry.artifact_ref,
        ", ".join(entry.conflicted_files()) or "no paths recorded",
    )
