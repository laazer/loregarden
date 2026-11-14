"""The doctor's check that every landed ticket's new files reached the base.

`integration/lg-milestone-that-491` lost lg-milestone-that-1316's landing
before the tree was published, and six files it added never reached main
though the ticket read done and delivered (lg-milestone-that-2624). It was
found by a measurement written by hand on 2026-11-14: for every landed ticket,
the files its landing added, against every path main's history ever added.
1316 was the only ticket with any missing. This is that measurement, kept.

"Ever added" rather than "present now": a file a later change deleted on
purpose was still delivered, and stays in the base's history. What the check
cannot see is a landing's edits to files that already existed; it measures
added files only, which is what caught 1316 (`landings_survive_their_branch`
is the ancestry check that sees the rest).

A landing its integration branch still holds and the base does not is waiting
to be published, not lost, and is skipped. Its own module for the reason
`doctor_docker` is: `doctor.py` is near its size cap. Read-only, and it does
not fetch: it reads the local `origin/<base>` and `<base>` refs as they are.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loregarden.models.domain import (
    DoctorCheck,
    DoctorFinding,
    DoctorStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import is_ancestor, rev
from loregarden.services.git_subprocess import run_git
from sqlmodel import Session, col, select

#: Tickets named in the finding before it says "and N more".
_NAMED = 5


@dataclass(frozen=True)
class LostFiles:
    """A done ticket whose landing added files no base history has ever had."""

    ticket: Ticket
    missing: tuple[str, ...]


@dataclass(frozen=True)
class LandingFileAudit:
    #: The base branch, and whether it exists here to be compared against.
    base: str
    has_base: bool
    lost: tuple[LostFiles, ...]
    #: Landings git could not read (a collected commit, a root commit): not
    #: cleared, so the finding says how many went unexamined.
    unreadable: tuple[str, ...]
    examined: int


def _git_lines(repo_root: Path, args: list[str]) -> list[str]:
    """Stdout lines of a read-only git command; raises `RuntimeError` when git fails."""
    result = run_git(args, cwd=str(repo_root), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()}")
    return [line for line in result.stdout.splitlines() if line]


def _base_tips(repo_root: Path, base: str) -> list[str]:
    tips: list[str] = []
    for ref in (f"refs/remotes/origin/{base}", f"refs/heads/{base}"):
        sha = rev(repo_root, ref)
        if sha and sha not in tips:
            tips.append(sha)
    return tips


def _ever_added(repo_root: Path, tips: list[str]) -> set[str]:
    """Every path any commit reachable from ``tips`` added."""
    return set(
        _git_lines(
            repo_root, ["log", "--format=", "--name-only", "--no-renames", "--diff-filter=A", *tips]
        )
    )


def _added_by(repo_root: Path, landed_sha: str) -> list[str]:
    """The files a landing added: its change against its first parent."""
    return _git_lines(
        repo_root,
        ["diff", "--name-only", "--no-renames", "--diff-filter=A", f"{landed_sha}^1", landed_sha],
    )


def _awaiting_publish(repo_root: Path, ticket: Ticket, base: str, tips: dict[str, str]) -> bool:
    """Whether the ticket's own branch still holds its landing and nothing recorded delivery.

    A landing on the base itself is delivered, never waiting.
    """
    if ticket.on_base_sha:
        return False
    branch = ticket.landed_branch
    if not branch or branch == base:
        return False
    tip = tips.setdefault(branch, rev(repo_root, f"refs/heads/{branch}"))
    return bool(tip) and (
        tip == ticket.landed_sha or is_ancestor(repo_root, ticket.landed_sha, tip)
    )


def audit_landed_files(session: Session, workspace: Workspace, repo_root: Path) -> LandingFileAudit:
    """Done tickets in ``workspace`` whose landing added files the base never had.

    With no base ref to compare against, nothing is examined and ``has_base``
    says so: every ticket would otherwise read lost. Raises `RuntimeError`
    when the base exists and git cannot read its history.
    """
    base = base_branch(workspace)
    base_tips = _base_tips(repo_root, base)
    if not base_tips:
        return LandingFileAudit(base=base, has_base=False, lost=(), unreadable=(), examined=0)
    history = _ever_added(repo_root, base_tips)

    tickets = session.exec(
        select(Ticket)
        .where(
            col(Ticket.workspace_id) == workspace.id,
            col(Ticket.state) == TicketState.DONE,
            col(Ticket.landed_sha) != "",
        )
        .order_by(col(Ticket.resolved_at))
    ).all()

    lost: list[LostFiles] = []
    unreadable: list[str] = []
    branch_tips: dict[str, str] = {}
    examined = 0
    for ticket in tickets:
        try:
            if _awaiting_publish(repo_root, ticket, base, branch_tips):
                continue
            added = _added_by(repo_root, ticket.landed_sha)
        except RuntimeError:  # silent-ok: counted in `unreadable` and named in the finding
            unreadable.append(ticket.external_id)
            continue
        examined += 1
        missing = tuple(path for path in added if path not in history)
        if missing:
            lost.append(LostFiles(ticket=ticket, missing=missing))
    return LandingFileAudit(
        base=base,
        has_base=True,
        lost=tuple(lost),
        unreadable=tuple(unreadable),
        examined=examined,
    )


def check_landed_files_reach_base(
    session: Session, workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    audit = audit_landed_files(session, workspace, repo_root)
    base = audit.base
    if not audit.has_base:
        return DoctorFinding(
            check=DoctorCheck.LANDED_FILES_REACH_BASE,
            status=DoctorStatus.WARN,
            finding=f"Neither origin/{base} nor {base} exists here, so no landing was examined.",
            remediation=(
                f"Set the workspace's base branch to the one its tickets land on, or create {base}."
            ),
        )
    skipped = (
        f" {len(audit.unreadable)} landing(s) git could not read went unexamined: "
        f"{', '.join(audit.unreadable[:_NAMED])}."
        if audit.unreadable
        else ""
    )
    if not audit.lost:
        status = DoctorStatus.WARN if audit.unreadable else DoctorStatus.PASS
        return DoctorFinding(
            check=DoctorCheck.LANDED_FILES_REACH_BASE,
            status=status,
            finding=(
                f"Every file the {audit.examined} delivered landing(s) added is in {base}'s "
                f"history.{skipped}"
            ),
            remediation=(
                "Check those commits still exist (git cat-file -e <landed_sha>)."
                if audit.unreadable
                else ""
            ),
        )
    named = "; ".join(
        f"{item.ticket.external_id} ({len(item.missing)}: {', '.join(item.missing[:3])}"
        f"{', …' if len(item.missing) > 3 else ''})"
        for item in audit.lost[:_NAMED]
    )
    more = f"; and {len(audit.lost) - _NAMED} more" if len(audit.lost) > _NAMED else ""
    return DoctorFinding(
        check=DoctorCheck.LANDED_FILES_REACH_BASE,
        status=DoctorStatus.FAIL,
        finding=(
            f"{len(audit.lost)} done ticket(s) added files that never reached {base}: "
            f"{named}{more}.{skipped}"
        ),
        remediation=(
            "Their landing was dropped before it was published. Restore the work from the "
            f"landing (git diff <landed_sha>^1 <landed_sha>) onto {base}, or reopen the ticket "
            "if the work is not wanted."
        ),
    )
