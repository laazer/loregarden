"""May this ticket start? Its prerequisites' work must be *reachable*, not just done.

`TicketDependencyService.unmet_prerequisites` (676) answers by state: a
prerequisite in `done` or `wont_do` is satisfied. State is the wrong signal
once landing exists. A prerequisite can be `done` with its branch never merged
— landing blocked, or a person closed it by hand — and then it delivers
nothing to the dependent: the 181 → 182 silent-hole case in 493.

Readiness here is read from git, not from a column. A prerequisite's work is
reachable when the tip of its branch is an ancestor of the dependent's target
branch or of the base branch. That covers every history at once: a ticket
that landed on the same integration branch, one whose tree published to
main, and one closed by hand before landing existed whose commits are on
main anyway. `landed_sha` (768) is the record of *how* it got there; this is
the check of *whether* it is there.

Judged by state alone, because there is no branch in this repository to ask:
- a prerequisite in another workspace;
- a prerequisite that never had a branch (a planning-only ticket);
- `wont_do`, which cannot be waited for.

The operator escape from 676 is untouched: only the orchestrator's choice of
the next child consults this. A named start is never held.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from loregarden.models.domain import Ticket, TicketState, Workspace
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_subprocess import run_git
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.target_branch import target_branch_name
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select


class UnmetReason(str, Enum):
    #: The prerequisite has not reached a satisfied state.
    NOT_DONE = "not_done"
    #: Done, but its branch carries work the dependent's base cannot see.
    NOT_LANDED = "not_landed"


@dataclass(frozen=True)
class UnmetPrerequisite:
    ticket: Ticket
    reason: UnmetReason
    #: The branch the dependent would need to see the work on — for the
    #: message: "done, not landed on integration/lg-ms-491".
    target: str = ""


def _rev(repo_root: Path, ref: str) -> str:
    result = run_git(
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    return (
        run_git(
            ["merge-base", "--is-ancestor", ancestor, descendant],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def work_is_reachable(
    repo_root: Path, prerequisite: Ticket, *, target: str, base: str
) -> bool | None:
    """Whether the prerequisite's branch tip is on ``target`` or ``base``.

    None when the prerequisite has no branch in this repository — there is
    nothing to be reachable, and the caller falls back to state.
    """
    branch_tip = _rev(repo_root, f"refs/heads/{resolve_ticket_branch(prerequisite)}")
    if not branch_tip:
        return None
    for ref in (target, base):
        if _is_ancestor(repo_root, branch_tip, _rev(repo_root, f"refs/heads/{ref}")):
            return True
    return False


def unmet_prerequisites_for_start(
    session: Session, ticket: Ticket, workspace: Workspace
) -> list[UnmetPrerequisite]:
    """The prerequisites holding ``ticket`` up, each with why."""
    dependencies = TicketDependencyService(session)
    prerequisite_ids = dependencies.prerequisites(ticket.id)
    if not prerequisite_ids:
        return []
    rows = session.exec(select(Ticket).where(Ticket.id.in_(prerequisite_ids))).all()

    repo_root = resolve_workspace_root(workspace)
    base = resolve_orchestration_profile(workspace).git.base_branch
    target = target_branch_name(session, ticket, workspace)

    unmet: list[UnmetPrerequisite] = []
    for prerequisite in rows:
        if prerequisite.state not in dependencies.SATISFIED_STATES:
            unmet.append(UnmetPrerequisite(prerequisite, UnmetReason.NOT_DONE))
            continue
        if prerequisite.state is TicketState.WONT_DO:
            continue
        if prerequisite.workspace_id != ticket.workspace_id:
            continue
        reachable = work_is_reachable(repo_root, prerequisite, target=target, base=base)
        if reachable is False:
            unmet.append(UnmetPrerequisite(prerequisite, UnmetReason.NOT_LANDED, target))
    return unmet
