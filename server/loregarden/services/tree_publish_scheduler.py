"""Publish a tree after every landing on it, without holding up the landing (958).

`publish_tree` used to run only when a tree's root completed, so an integration
branch lived as long as its milestone and drifted from the base the whole time.
A landing now schedules it. The publish pushes, which runs the pre-push suite
(measured at up to 37 minutes), so it runs on a thread of its own, inline under
``LOREGARDEN_SYNC_RUNS`` like every other background turn.

One publish per root at a time. A landing that arrives while one is running
asks for another, which runs once the first finishes, so the published tip is
never older than the last landing.

A failed publish puts a card in front of a person, naming the step that failed
and what finishes it. A publish of that root that succeeds closes it, from any
path (`publish_and_settle`), and so does the reconcile sweep once the base
holds the branch's work (`close_published_cards`): a PR merged by hand, in a
tree with nothing left to land, otherwise kept its card for good (463's #620,
2026-10-15). Nothing here blocks the ticket that landed: its own work is done,
and the next landing (or the root's completion) publishes again.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

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
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.prepared_action import PreparedAction
from loregarden.services.publish_tree import PublishOutcome, publish_tree, published_branch
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.tree_ux_section import MISSING_UX_CARD_PREFIX
from loregarden.services.work_landed import landing_commit
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, or_, select

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
            publish_and_settle(session, root, workspace)
    except Exception as exc:  # noqa: BLE001 — a thread's crash must reach the card, not vanish
        logger.exception("Tree publish for %s crashed", root_id)
        _report_crash(root_id, exc)


def publish_and_settle(session: Session, root: Ticket, workspace: Workspace) -> PublishOutcome:
    """Publish ``root``'s tree, and close its card or file one by how that went.

    Every path that publishes comes through here, so a card closes on whichever
    publish succeeds, not only the scheduler's (491's and 463's cards outlived
    publishes from `loregarden_land_ticket`).
    """
    outcome = publish_tree(session, root, workspace)
    if outcome.ok:
        close_publish_card(session, root)
        return outcome
    logger.warning(
        "Publishing %s for %s failed: %s", outcome.branch, root.external_id, outcome.detail
    )
    report_publish_failure(session, root, outcome)
    return outcome


#: Every publish card's title starts with this; the sweep finds them by it.
PUBLISH_CARD_PREFIX = "Publish failed: "


def publish_card_title(root: Ticket) -> str:
    return f"{PUBLISH_CARD_PREFIX}{root.external_id or root.id[:8]}"


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
    """File the card, or refresh the pending one with the latest reason and action."""
    where, command = _what_failed(root, outcome)
    impact = (
        f"{where}\n\n{outcome.detail}\n\n"
        "The next landing in this tree, or the root's completion, publishes again. This card "
        "closes when one succeeds, or once the base holds the branch's work."
    )
    action = PreparedAction(
        tier=HumanActionTier.MANUAL,
        attempted=f"Published {outcome.branch or 'the tree'} after a landing.",
        command=command,
    )
    existing = _pending(session, root)
    if existing:
        card = existing[0]
        # The command follows the same outcome as the impact, so one check covers both.
        if card.impact != impact:
            card.impact = impact
            card.tool_name = command
            card.tool_input_json = action.model_dump_json()
            session.add(card)
            session.commit()
        return card
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


def _what_failed(root: Ticket, outcome: PublishOutcome) -> tuple[str, str]:
    """Where the work stands after ``outcome``, and the command that finishes it.

    Each step of the chain needs the one before it, so the step that failed
    says how far the work got. A card that said "not on its way to the base"
    and suggested a push, for a pushed branch whose PR was open and mergeable,
    sent a person to redo what had worked (463's #620, where only enabling
    auto-merge was refused).
    """
    branch = outcome.branch or "the tree's branch"
    republish = (
        f"./scripts/loregarden-cli.sh mcp call loregarden_land_ticket ticket_id={root.external_id}"
    )
    if outcome.pr_url:
        where = (
            f"`{branch}` is pushed and its PR is open: {outcome.pr_url}. The work reaches the "
            f"base when that PR merges; only `{outcome.failed_step or 'a later step'}` failed."
        )
        if outcome.failed_step == "auto_merge":
            return where, f"gh pr merge {outcome.pr_url} --squash"
        return where, ""
    if outcome.failed_step == "open_pr":
        return (
            f"`{branch}` is pushed, but no PR was opened for it, so nothing will merge it into "
            "the base. Publishing again finds or opens the PR.",
            republish,
        )
    if outcome.failed_step == "push":
        return (
            f"`{branch}` was not pushed, so the work that landed on it is not on its way to the "
            "base. Publish again rather than pushing the branch by hand: the publish pushes one "
            "commit cut from the base, not the branch's history.",
            republish,
        )
    return (
        f"`{branch}` was not published: no commit could be cut to publish it, so the work that "
        "landed on it is not on its way to the base. Clear the cause below, then publish again.",
        republish,
    )


def close_publish_card(session: Session, root: Ticket, *, reason: str = "") -> int:
    """Close the pending publish card on ``root``, if any. Returns how many closed."""
    return _resolve(session, root, _pending(session, root), reason=reason)


def _resolve(session: Session, root: Ticket, cards: list[Approval], *, reason: str) -> int:
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
            payload={"approval_id": card.id, "reason": reason or "published"},
        )
    return len(cards)


def close_published_cards(session: Session) -> int:
    """Close each pending publish or UX-answers card whose base already holds its tree's work.
    Returns how many closed. The reconciler reaches this as the
    `publish_cards_close_once_landed` invariant (`services.invariant_registry`)."""
    return close_held_cards(session, published_cards_held(session))


@dataclass(frozen=True)
class HeldCard:
    """A pending publish or UX-answers card, its root, and how the base already holds the work."""

    card: Approval
    root: Ticket
    held: str


def published_cards_held(session: Session) -> list[HeldCard]:
    """Pending cards about a tree's publish whose base already holds what it publishes.

    Read-only. The UX-answers card is one: it asks for answers for the next
    publish, and a tree the base holds has none, so nothing else would close it.
    """
    cards = session.exec(
        select(Approval).where(
            Approval.kind == ApprovalKind.HUMAN_ACTION,
            Approval.status == ApprovalStatus.PENDING,
            or_(
                col(Approval.title).startswith(PUBLISH_CARD_PREFIX),
                col(Approval.title).startswith(MISSING_UX_CARD_PREFIX),
            ),
        )
    ).all()
    held_cards: list[HeldCard] = []
    for card in cards:
        root = session.get(Ticket, card.ticket_id) if card.ticket_id else None
        workspace = workspace_for_ticket(session, root) if root else None
        if root is None or workspace is None:
            logger.warning("Publish card %s names no ticket or workspace; leaving it", card.id)
            continue
        held = _base_holds(root, workspace)
        if held:
            held_cards.append(HeldCard(card, root, held))
    return held_cards


def close_held_cards(session: Session, held_cards: list[HeldCard]) -> int:
    """Close each of ``held_cards``. Returns how many closed."""
    closed = 0
    for item in held_cards:
        logger.info("Closing %s's card %r: %s", item.root.external_id, item.card.title, item.held)
        closed += _resolve(session, item.root, [item.card], reason=item.held)
    return closed


def _base_holds(root: Ticket, workspace: Workspace) -> str:
    """How the base already holds what ``root`` publishes, or "" when it does not.

    The remote's base first: a PR merged on GitHub is there as soon as anyone
    fetches, before the local base is pulled. A squash merge shares no commit
    with the branch, so ancestry alone misses it; `landing_commit` does not.
    """
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return ""
    branch, tip = published_branch(repo_root, root)
    if not tip:
        return ""
    base = base_branch(workspace)
    for ref in (f"refs/remotes/origin/{base}", f"refs/heads/{base}"):
        base_sha = rev(repo_root, ref)
        if not base_sha:
            continue
        try:
            holder = landing_commit(repo_root, tip, base_sha)
        except RuntimeError as exc:
            # silent-ok: retried on the next reconcile pass; the card stays open meanwhile
            logger.warning("Could not tell whether %s holds %s: %s", ref, branch, exc)
            continue
        if holder == tip:
            return f"{ref} contains {branch}"
        if holder:
            return f"{ref} holds everything {branch} adds, in {holder[:12]}"
    return ""


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
