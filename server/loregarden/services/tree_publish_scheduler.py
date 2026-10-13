"""Publish a tree after every landing on it, without holding up the landing (958).

`publish_tree` used to run only when a tree's root completed, so an integration
branch lived as long as its milestone and drifted from the base the whole time.
A landing now schedules it. The publish pushes, which runs the pre-push suite
(measured at up to 37 minutes), so it runs on a thread of its own, inline under
``LOREGARDEN_SYNC_RUNS`` like every other background turn.

One publish per root at a time. A landing that arrives while one is running
asks for another, which runs once the first finishes, so the published tip is
never older than the last landing.

A failed publish puts a card in front of a person, naming why. It stays until a
publish of that root succeeds, which closes it. Nothing here blocks the ticket
that landed: its own work is done, and the next landing (or the root's
completion) publishes again.
"""

from __future__ import annotations

import logging
import os
import threading

from loregarden.core.event_bus import event_bus
from loregarden.db.session import engine
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    EventType,
    HumanActionTier,
    Ticket,
    Workspace,
    utcnow,
)
from loregarden.services.drain import is_draining, refuse_reason
from loregarden.services.prepared_action import PreparedAction
from loregarden.services.publish_tree import PublishOutcome, publish_tree
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

RESOLVED_BY_PUBLISH = "automation"

_GUARD = threading.Lock()
#: Root ids with a publish running in this process.
_IN_FLIGHT: set[str] = set()
#: Root ids that landed again while their publish ran.
_AGAIN: set[str] = set()


def schedule_tree_publish(root_id: str, workspace_id: str) -> None:
    """Publish the tree rooted at ``root_id``, now or right after the one running."""
    if is_draining():
        logger.warning("Not publishing tree %s: %s", root_id, refuse_reason())
        return
    with _GUARD:
        if root_id in _IN_FLIGHT:
            _AGAIN.add(root_id)
            return
        _IN_FLIGHT.add(root_id)
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        run_tree_publish(root_id, workspace_id)
        return
    try:
        threading.Thread(
            target=run_tree_publish,
            args=(root_id, workspace_id),
            name=f"loregarden-tree-publish-{root_id[:8]}",
            daemon=True,
        ).start()
    except RuntimeError:
        with _GUARD:
            _IN_FLIGHT.discard(root_id)
        raise


def run_tree_publish(root_id: str, workspace_id: str) -> None:
    """Publish until no landing is waiting on this root, then release it."""
    try:
        while True:
            _publish_once(root_id, workspace_id)
            with _GUARD:
                if root_id not in _AGAIN:
                    return
                _AGAIN.discard(root_id)
    finally:
        with _GUARD:
            _IN_FLIGHT.discard(root_id)
            _AGAIN.discard(root_id)


def _publish_once(root_id: str, workspace_id: str) -> None:
    try:
        with Session(engine) as session:
            root = session.get(Ticket, root_id)
            workspace = session.get(Workspace, workspace_id)
            if root is None or workspace is None:
                logger.warning(
                    "Tree publish skipped: root %s or workspace %s no longer exists",
                    root_id,
                    workspace_id,
                )
                return
            outcome = publish_tree(session, root, workspace)
            if outcome.ok:
                close_publish_card(session, root)
            else:
                logger.warning(
                    "Publishing %s for %s failed: %s",
                    outcome.branch,
                    root.external_id,
                    outcome.detail,
                )
                report_publish_failure(session, root, outcome)
    except Exception as exc:  # noqa: BLE001 — a thread's crash must reach the card, not vanish
        logger.exception("Tree publish for %s crashed", root_id)
        _report_crash(root_id, exc)


def publish_card_title(root: Ticket) -> str:
    return f"Publish failed: {root.external_id or root.id[:8]}"


def _pending(session: Session, root: Ticket) -> list[Approval]:
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == publish_card_title(root),
            )
        ).all()
    )


def report_publish_failure(session: Session, root: Ticket, outcome: PublishOutcome) -> Approval:
    """File the card, or refresh the pending one with the latest reason."""
    impact = (
        f"Publishing `{outcome.branch or 'the tree'}` to the base failed, so the work that "
        f"landed on it is not on its way to the base:\n\n{outcome.detail}\n\n"
        "The next landing in this tree, or the root's completion, publishes again; this card "
        "closes when one succeeds."
    )
    existing = _pending(session, root)
    if existing:
        card = existing[0]
        if card.impact != impact:
            card.impact = impact
            session.add(card)
            session.commit()
        return card
    action = PreparedAction(
        tier=HumanActionTier.MANUAL,
        attempted=f"Published {outcome.branch} after a landing; the publish chain failed.",
        command=f"git push -u origin {outcome.branch}" if outcome.branch else "",
    )
    card = Approval(
        ticket_id=root.id,
        workspace_id=root.workspace_id,
        kind=ApprovalKind.HUMAN_ACTION,
        title=publish_card_title(root),
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
        workspace_id=root.workspace_id,
        ticket_id=root.id,
        payload={"approval_id": card.id, "branch": outcome.branch},
    )
    return card


def close_publish_card(session: Session, root: Ticket) -> int:
    """Close the pending publish card on ``root``, if any. Returns how many closed."""
    cards = _pending(session, root)
    for card in cards:
        card.status = ApprovalStatus.APPROVED
        card.resolved_by = RESOLVED_BY_PUBLISH
        card.resolved_at = utcnow()
        session.add(card)
    if not cards:
        return 0
    session.commit()
    for card in cards:
        event_bus.publish(
            session,
            EventType.APPROVAL_RESOLVED,
            workspace_id=root.workspace_id,
            ticket_id=root.id,
            payload={"approval_id": card.id},
        )
    return len(cards)


def _report_crash(root_id: str, exc: Exception) -> None:
    try:
        with Session(engine) as session:
            root = session.get(Ticket, root_id)
            if root is not None:
                report_publish_failure(
                    session,
                    root,
                    PublishOutcome(ok=False, branch="", detail=f"the publish crashed: {exc}"),
                )
    except Exception:  # noqa: BLE001 — logged with traceback; the crash above is already logged too
        logger.exception("Could not record the crashed publish for %s", root_id)
