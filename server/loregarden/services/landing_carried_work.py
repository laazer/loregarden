"""A landing that merged nothing is not a landing for a ticket that wrote code.

`land_ticket` has two ok exits that merge nothing: the ticket has no branch,
and every commit on the branch is already on the target. Both derived done.
That is right for a ticket that never meant to change the repository (a
survey pass writes documents) and for a branch whose work an earlier landing
already delivered, and wrong for a ticket whose runs wrote files that no
commit on its branch carries. Of 101 non-survey tickets landed in October,
lg-skills-versioned-328's implement stage recorded edits to the server and
its branch was a commit of `main`; lg-external-harness-489's branch was its
integration branch's own refresh commit, and the tests its stages wrote
reached `main` two days later only because a person opened #582 by hand.

**Expected to change the repository** is read from what the ticket's runs
recorded (`ticket_recorded_paths`): a run that wrote a path claimed it. 176
of the 177 survey passes landed in October recorded none (the one that did
merged commits); 86 of the 95 non-survey tickets that merged work did. An
empty record cannot say "nothing was written" (most runs before lg-workflow-integrity-675 recorded
nothing), so it decides nothing and the landing goes through as before.

**Carried nothing** is a branch with no commit of its own: walking its first
parents, every non-merge commit is already on the base. A ticket's commits sit
on that walk; the merges that refreshed it from its target do not count, and
an integration branch's own history is merges (`Land …`, `Refresh … from
main`) above the base it was cut from. A branch whose work reached the target
by a squash or a cherry-pick still has commits the base lacks.

**A landing on record** answers before the walk does. A workspace that
publishes with merge commits (blobert) puts the ticket's own commits on the
base, so a re-land there walks to nothing; and a published integration branch
is squashed and moved (#621), so a recorded merge need not be on the target
any more. With this guard in place `landed_sha` is written only by a merge or
by a skip that passed it, so the record is the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loregarden.models.domain import Ticket, TicketState
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.git_subprocess import run_git
from loregarden.services.recorded_paths import ticket_recorded_paths
from loregarden.services.target_branch import target_branch_name
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

#: How many recorded paths a refusal names before it counts the rest.
_NAMED_PATHS = 3


def carries_own_work(repo_root: Path, branch_sha: str, base: str) -> bool:
    """Whether ``branch_sha`` has a non-merge commit, on its first parents, not on ``base``.

    ``base`` is excluded both as the local branch and as its `origin` tracking
    ref, since a local base behind the remote would count the remote's commits
    as the branch's. Raises `RuntimeError` when git cannot answer: "could not
    tell" is not "nothing there", and the caller blocks on it either way.
    """
    excluded = [
        ref for ref in (f"refs/heads/{base}", f"refs/remotes/origin/{base}") if rev(repo_root, ref)
    ]
    done = run_git(
        [
            "rev-list",
            "--first-parent",
            "--no-merges",
            "--max-count=1",
            branch_sha,
            "--not",
            *excluded,
        ],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or "git rev-list failed").strip())
    return bool(done.stdout.strip())


def empty_landing(
    session: Session,
    ticket: Ticket,
    repo_root: Path,
    *,
    branch: str,
    target: str,
    base: str,
) -> str | None:
    """Why landing ``ticket`` would merge nothing it was meant to deliver, or None.

    None when the ticket's runs recorded no path, when a landing on
    ``target`` is on record, or when the branch has a commit of its own.
    """
    recorded = ticket_recorded_paths(session, ticket)
    if not recorded or _landed_on_record(ticket, target):
        return None
    wrote = _describe_paths(recorded)
    branch_sha = rev(repo_root, f"refs/heads/{branch}")
    if not branch_sha:
        return (
            f"Not landed: this ticket's runs changed {wrote}, but its branch {branch} does "
            "not exist, so no commit carries that work. Commit it to the ticket's branch and "
            "requeue."
        )
    try:
        if carries_own_work(repo_root, branch_sha, base):
            return None
    except RuntimeError as exc:
        return f"Not landed: could not tell whether {branch} carries any work of its own: {exc}"
    return (
        f"Not landed: this ticket's runs changed {wrote}, but {branch} has no commit of its "
        f"own — every commit on it is already on {base} — so landing it on {target} would "
        f"merge nothing. Commit the work to {branch} and requeue; if it reached {base} by "
        "another change, close the ticket naming that change."
    )


@dataclass(frozen=True)
class EmptyLanding:
    ticket: Ticket
    reason: str


def done_tickets_that_landed_nothing(session: Session, since: datetime) -> list[EmptyLanding]:
    """Done tickets, resolved at or after ``since``, that `empty_landing` would refuse now.

    The same exits `land_ticket` checks: any target for a missing branch, and
    an integration target for a branch that exists — a base target is the
    publish leg's, which never reaches the check. ``since`` keeps the backlog
    from before the guard out of a report-only sweep that would otherwise name
    it on every pass.
    """
    done = session.exec(
        select(Ticket).where(
            Ticket.state == TicketState.DONE,
            col(Ticket.workspace_id).is_not(None),
            col(Ticket.resolved_at) >= since.replace(tzinfo=None),
        )
    ).all()
    found: list[EmptyLanding] = []
    for ticket in done:
        workspace = workspace_for_ticket(session, ticket)
        if workspace is None:
            continue
        repo_root = resolve_workspace_root(workspace)
        if not (repo_root / ".git").exists():
            continue
        branch = resolve_ticket_branch(ticket)
        target = target_branch_name(session, ticket, workspace)
        base = base_branch(workspace)
        if target == base and rev(repo_root, f"refs/heads/{branch}"):
            continue
        reason = empty_landing(session, ticket, repo_root, branch=branch, target=target, base=base)
        if reason is not None:
            found.append(EmptyLanding(ticket=ticket, reason=reason))
    return found


def _landed_on_record(ticket: Ticket, target: str) -> bool:
    """A landing on ``target`` is on record.

    Not re-checked against the target's history, for the squash-and-move the
    module docstring names: lg-durable-remote-336 and lg-bug-hole-574 landed
    real merges, lost their branch afterwards, and would read as never landed.
    """
    return ticket.landed_branch == target and bool(ticket.landed_sha)


def _describe_paths(paths: set[str]) -> str:
    named = sorted(paths)[:_NAMED_PATHS]
    rest = len(paths) - len(named)
    listed = ", ".join(named) + (f" and {rest} more" if rest else "")
    return f"{len(paths)} file(s) ({listed})"
