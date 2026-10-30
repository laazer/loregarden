"""The "User-facing surfaces" section of a tree's PR, assembled from its tickets' answers.

`publish_tree` writes a tree's PR description, and a PR that changes a
client page or component must carry this section or the "PR UX section"
check fails, merge blocked, whoever was meant to merge it (#622, 2026-10-15).
Each ticket records its own answers (`ux_answers`); this finds which ticket's
landing changed which surface file, and writes each one's answers under its
name.

A ticket with no answers is named in the section and on a card, never
covered by a waiver written here: "no user-visible change" is a claim only the
ticket can make.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

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
from loregarden.services.git_subprocess import run_git
from loregarden.services.hierarchy_service import descendants_by_root
from loregarden.services.prepared_action import PreparedAction
from loregarden.services.ux_answers import UxAnswers, is_surface, latest_ux_answers
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

SECTION_HEADING = "## User-facing surfaces"
RECORD_TOOL = "loregarden_record_ux_answers"
RESOLVED_BY_PUBLISH = "automation"
#: Titles every card `report_missing_ux_answers` files; `tree_publish_scheduler`
#: closes one once the base holds its tree, since no later publish will.
MISSING_UX_CARD_PREFIX = "UX answers missing: "


@dataclass(frozen=True)
class SurfaceOwner:
    """A ticket whose landing changed user-facing files, and what it recorded."""

    ticket: Ticket
    files: tuple[str, ...]
    answers: UxAnswers | None
    #: Why recorded answers could not be used; "" when they could, or there are none.
    problem: str = ""

    @property
    def answered(self) -> bool:
        return self.answers is not None


@dataclass(frozen=True)
class TreeUxSection:
    """The section to append to the PR body ("" when no surface changed)."""

    text: str = ""
    owners: tuple[SurfaceOwner, ...] = ()

    @property
    def missing(self) -> tuple[SurfaceOwner, ...]:
        return tuple(owner for owner in self.owners if not owner.answered)


def _changed(repo_root: Path, older: str, newer: str) -> list[str]:
    listed = run_git(
        ["diff", "--name-only", "--no-renames", older, newer],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        raise RuntimeError(
            f"git diff failed for {older[:12]}..{newer[:12]}: "
            f"{(listed.stderr or listed.stdout).strip()}"
        )
    return [line for line in listed.stdout.splitlines() if line]


def _landing_files(repo_root: Path, landed_sha: str) -> set[str]:
    """What one landing merge brought onto the branch: the diff from its first parent.

    A landing that merged nothing records the branch tip, which has one parent;
    its files cannot be told from the commit, so it claims none.
    """
    parents = run_git(
        ["rev-list", "--parents", "-n1", landed_sha],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if parents.returncode != 0:
        raise RuntimeError(
            f"git rev-list failed for {landed_sha[:12]}: "
            f"{(parents.stderr or parents.stdout).strip()}"
        )
    shas = parents.stdout.split()
    if len(shas) != 3:
        return set()
    return set(_changed(repo_root, shas[1], landed_sha))


def _owner(session: Session, ticket: Ticket, files: list[str]) -> SurfaceOwner:
    try:
        answers = latest_ux_answers(session, ticket)
    except ValueError as exc:
        return SurfaceOwner(ticket, tuple(files), None, problem=str(exc))
    return SurfaceOwner(ticket, tuple(files), answers)


def surface_owners(
    session: Session, repo_root: Path, root: Ticket, *, branch: str, surfaces: list[str]
) -> list[SurfaceOwner]:
    """Each landed ticket in ``root``'s tree with the surface files its landing changed.

    Files no landing accounts for (the root's own folded commits) are the root's.
    """
    remaining = list(surfaces)
    owners: list[SurfaceOwner] = []
    for ticket in descendants_by_root(session, [root.id])[root.id]:
        if ticket.landed_branch != branch or not ticket.landed_sha:
            continue
        brought = _landing_files(repo_root, ticket.landed_sha)
        files = [path for path in remaining if path in brought]
        if files:
            owners.append(_owner(session, ticket, files))
            remaining = [path for path in remaining if path not in files]
    if remaining:
        owners.append(_owner(session, root, remaining))
    return owners


def _block(owner: SurfaceOwner) -> list[str]:
    ticket = owner.ticket
    lines = [
        f"#### {ticket.external_id or ticket.id[:8]}: {ticket.title}",
        "Files: " + ", ".join(f"`{path}`" for path in owner.files),
        "",
    ]
    if owner.answers is not None:
        return [*lines, *owner.answers.lines, ""]
    reason = owner.problem or "No UX answers recorded."
    return [
        *lines,
        f"_{reason} Record them with `{RECORD_TOOL}` for "
        f"`{ticket.external_id or ticket.id}`; the next publish fills this in._",
        "",
    ]


def tree_ux_section(
    session: Session,
    repo_root: Path,
    root: Ticket,
    *,
    branch: str,
    base_sha: str,
    head_sha: str,
) -> TreeUxSection:
    """The section for publishing ``head_sha`` over ``base_sha``; empty when no surface changed.

    Raises ``RuntimeError`` when git cannot say what changed: a PR described
    from a guess is worse than a publish that reports it could not tell.
    """
    surfaces = [path for path in _changed(repo_root, base_sha, head_sha) if is_surface(path)]
    if not surfaces:
        return TreeUxSection()
    owners = tuple(surface_owners(session, repo_root, root, branch=branch, surfaces=surfaces))
    lines = [
        "",
        SECTION_HEADING,
        "",
        f"_Assembled by Loregarden from the UX answers each ticket recorded (`{RECORD_TOOL}`)._",
        "",
    ]
    for owner in owners:
        lines += _block(owner)
    return TreeUxSection("\n".join(lines), owners)


# --- the card when answers are missing -------------------------------------------


def missing_card_title(root: Ticket) -> str:
    return f"{MISSING_UX_CARD_PREFIX}{root.external_id or root.id[:8]}"


def _pending(session: Session, root: Ticket) -> list[Approval]:
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == missing_card_title(root),
            )
        ).all()
    )


def report_missing_ux_answers(
    session: Session, root: Ticket, missing: tuple[SurfaceOwner, ...], pr_url: str
) -> Approval:
    """File the card, or refresh the pending one, naming each ticket without answers."""
    names = [
        f"- `{owner.ticket.external_id or owner.ticket.id}` ({', '.join(owner.files)})"
        + (f": {owner.problem}" if owner.problem else "")
        for owner in missing
    ]
    impact = (
        f'{pr_url or "The tree PR"} changes user-facing files, and its "PR UX section" check '
        "needs the question, action and real data for each change. These tickets have none "
        f"recorded:\n\n" + "\n".join(names) + "\n\n"
        f"Record them with `{RECORD_TOOL}` (or `no_change_reason` where nobody can see it). "
        "The next publish of the tree writes them into the PR and closes this card; so does "
        "the base taking the tree's work in, since nothing publishes after that."
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
        attempted="Assembled the tree PR's user-facing surfaces section; some tickets had no answers.",
        command=RECORD_TOOL,
    )
    card = Approval(
        ticket_id=root.id,
        workspace_id=root.workspace_id,
        kind=ApprovalKind.HUMAN_ACTION,
        title=missing_card_title(root),
        level="medium",
        impact=impact,
        tool_name=action.command,
        tool_input_json=action.model_dump_json(),
        status=ApprovalStatus.PENDING,
    )
    session.add(card)
    session.commit()
    session.refresh(card)
    logger.warning(
        "Tree PR for %s changes user-facing files with no UX answers from %s; filed card %s",
        root.external_id,
        ", ".join(owner.ticket.external_id for owner in missing),
        card.id,
    )
    event_bus.publish(
        session,
        EventType.APPROVAL_REQUESTED,
        workspace_id=root.workspace_id,
        ticket_id=root.id,
        payload={"approval_id": card.id, "pr_url": pr_url},
    )
    return card


def close_missing_ux_answers(session: Session, root: Ticket) -> int:
    """Close the pending card on ``root``, if any. Returns how many closed."""
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
