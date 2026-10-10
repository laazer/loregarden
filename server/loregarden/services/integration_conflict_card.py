"""One inbox card per tree whose integration branch cannot take its base.

A tree's integration branch is refreshed from the base every time a ticket in
the tree is cut. When the base moves under it with a conflicting change, every
stage run in the tree refuses to start and, before this module, the only place
that said so was whichever run or chat turn happened to hit it next — once as
"Baxter unavailable: …" in a chat reply, with no ticket blocked and nothing in
the inbox (lg-durable-remote-335, after #560 changed `RunLogModal.tsx` on
`main`).

The card sits on the tree's root, names the branch and the files, and says what
to do. It is filed by whoever finds the conflict first (the periodic sweep in
`integration_branch_health`, or `target_branch.resolve_target_branch` on a cut)
and resolved by the sweep once the branch takes the base cleanly, so it never
outlives the problem it reports.

It does not block the root: the root is usually a milestone with children in
flight, and the conflict already stops each of them at its next cut. The card
is the one place a person learns why.

Kept free of `target_branch` imports so `target_branch` can file through it.
"""

from __future__ import annotations

import logging

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    HumanActionTier,
    Ticket,
    utcnow,
)
from loregarden.services.prepared_action import PreparedAction
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: `Approval.resolved_by` for a card the sweep closed because the conflict is gone.
RESOLVED_BY_SWEEP = "automation"


def conflict_card_title(branch: str, base: str) -> str:
    """The card's title, and the key that keeps it to one per branch."""
    return f"Merge conflict: {branch} cannot take {base}"


def _message(branch: str, base: str, files: tuple[str, ...]) -> str:
    listed = "\n".join(f"- {path}" for path in files) or "- (git named no paths)"
    return (
        f"`{base}` moved and no longer merges into `{branch}`. Conflicting files:\n"
        f"{listed}\n\n"
        f"Until this is resolved, every stage run in this tree refuses to start, and chat "
        f"turns run on `{branch}` as it is, without `{base}`'s newer commits.\n\n"
        f"Resolve it by merging `{base}` into `{branch}` in a throwaway worktree, fixing the "
        f"files above, and committing. This card closes itself once the branch takes "
        f"`{base}` cleanly."
    )


def _command(branch: str, base: str) -> str:
    worktree = f"/tmp/{branch.replace('/', '-')}"
    return f"git worktree add {worktree} {branch} && git -C {worktree} merge {base}"


def _pending(session: Session, root: Ticket, title: str) -> list[Approval]:
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == title,
            )
        ).all()
    )


def report_integration_conflict(
    session: Session, root: Ticket, *, branch: str, base: str, files: tuple[str, ...]
) -> Approval:
    """File the card, or refresh the one already pending. Returns it."""
    title = conflict_card_title(branch, base)
    message = _message(branch, base, files)
    existing = _pending(session, root, title)
    if existing:
        card = existing[0]
        if card.impact != message:
            card.impact = message
            session.add(card)
            session.commit()
        return card

    action = PreparedAction(
        tier=HumanActionTier.MANUAL,
        attempted=f"Merged {base} into {branch} without a checkout; git reported conflicts.",
        command=_command(branch, base),
    )
    card = Approval(
        ticket_id=root.id,
        workspace_id=root.workspace_id,
        kind=ApprovalKind.HUMAN_ACTION,
        title=title,
        level="high",
        impact=message,
        tool_name=action.command,
        tool_input_json=action.model_dump_json(),
        status=ApprovalStatus.PENDING,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    logger.warning(
        "Integration branch %s cannot take %s (conflicts in %s); filed inbox card %s on %s",
        branch,
        base,
        ", ".join(files),
        card.id,
        root.external_id,
    )
    event_bus.publish(
        session,
        EventType.APPROVAL_REQUESTED,
        workspace_id=root.workspace_id,
        ticket_id=root.id,
        payload={"approval_id": card.id, "branch": branch, "base": base, "files": list(files)},
    )
    return card


def clear_integration_conflict(session: Session, root: Ticket, *, branch: str, base: str) -> int:
    """Close the pending card for ``branch``, if any. Returns how many closed."""
    cards = _pending(session, root, conflict_card_title(branch, base))
    for card in cards:
        card.status = ApprovalStatus.APPROVED
        card.resolved_by = RESOLVED_BY_SWEEP
        card.resolved_at = utcnow()
        session.add(card)
    if not cards:
        return 0
    session.commit()
    for card in cards:
        logger.info("Integration branch %s takes %s again; closed card %s", branch, base, card.id)
        event_bus.publish(
            session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=root.workspace_id,
            ticket_id=root.id,
            payload={"approval_id": card.id, "branch": branch, "base": base},
        )
    return len(cards)
