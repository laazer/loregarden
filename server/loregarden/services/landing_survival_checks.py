"""The `landings_survive_their_branch` detector, apart from the guard it backs.

Split out of `landing_survival` because the guard is imported by the branch-move
owner (`integration_branch_moves`), which nearly every landing path imports,
and the detector's `invariants` import reaches back into those paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import Ticket, TicketState
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.base_delivery import base_shas, holder
from loregarden.services.doctor_landings import base_tips, ever_added, files_dropped_at
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.invariants import Violation
from loregarden.services.landing_survival import contains, undelivered
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass
class _BaseView:
    """What the base holds, read once per workspace and only when a check needs it."""

    repo_root: Path
    name: str
    shas: tuple[str, ...]
    _history: set[str] | None = None

    def ever_had(self, path: str) -> bool:
        if self._history is None:
            self._history = ever_added(self.repo_root, base_tips(self.repo_root, self.name))
        return path in self._history


def _why_lost(repo_root: Path, ticket: Ticket, tip: str, base: _BaseView) -> str:
    """Why ``ticket``'s landing is held by neither ``tip`` nor the base; "" when it is held.

    Raises `RuntimeError` when git cannot tell.
    """
    branch = ticket.landed_branch
    if contains(repo_root, tip, ticket.landed_sha):
        dropped = files_dropped_at(repo_root, ticket, tip)
        gone = [path for path in dropped if not base.ever_had(path)]
        if not gone:
            return ""
        return (
            f"{ticket.external_id} is done and {branch} still has its landing "
            f"{ticket.landed_sha[:12]} in its history, but a later commit there deleted "
            f"{len(gone)} file(s) it added that {base.name} never had: {', '.join(gone[:5])}"
        )
    if holder(repo_root, ticket.landed_sha, base.shas):
        return ""
    return (
        f"{ticket.external_id} is done, landed as {ticket.landed_sha[:12]} on {branch}, "
        f"but neither {branch} nor {base.name} holds that work any more"
    )


def landings_lost_from_their_branch(session: Session) -> list[Violation]:
    """Done tickets whose landing is on neither their integration branch nor the base.

    Only while the branch exists: see the comment on a missing one below.

    `entered_at` is the ticket's `resolved_at`: the loss happened later, at a
    moment nothing recorded, so a loss after `since` of a ticket resolved
    before it reads as backlog.
    """
    violations: list[Violation] = []
    for workspace in active_workspaces(session):
        repo_root = resolve_workspace_root(workspace)
        if not (repo_root / ".git").exists():
            continue
        done = [t for t in undelivered(session, workspace) if t.state is TicketState.DONE]
        if not done:
            continue
        name = base_branch(workspace)
        base = _BaseView(repo_root, name, base_shas(repo_root, name))
        tips: dict[str, str] = {}
        for ticket in done:
            branch = ticket.landed_branch
            tip = tips.setdefault(branch, rev(repo_root, f"refs/heads/{branch}"))
            if not tip:
                # Not knowable: with the tree gone, a landing asked on its own reads
                # "not held" whenever a later sibling rewrote its lines, so 804, on
                # main since its September publish, would read lost forever. The
                # deletion that would make this a loss is refused now.
                continue
            try:
                detail = _why_lost(repo_root, ticket, tip, base)
            except RuntimeError as exc:
                # silent-ok: asked again next pass; a check git could not run is not a violation
                logger.warning("Could not tell where %s's landing is: %s", ticket.external_id, exc)
                continue
            if detail:
                violations.append(
                    Violation(
                        subject_id=ticket.id,
                        ticket_id=ticket.id,
                        entered_at=ticket.resolved_at,
                        detail=detail,
                    )
                )
    return violations
