"""Code no import reaches, kept because an unfinished ticket needs it.

The module says so itself, in a `kept-for: <ticket external id> - <reason>`
comment that the `ts-unreachable` gate requires of every client module the app
does not import. This reads those markers back from the ticket's side: whoever
takes the ticket is told the code exists, and the doctor reports a marker whose
ticket is over (`check_kept_code_tickets`).

Read with `git grep` over the checkout's tracked files, so there is no second
record to drift from the source. Test files are skipped: they quote markers.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from loregarden.core.state_machine import StateMachine
from loregarden.models.domain import (
    DoctorCheck,
    DoctorFinding,
    DoctorStatus,
    Ticket,
    Workspace,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.ticket_ids import AmbiguousTicketReference, TicketNotFound, resolve_one
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

#: The line's shape, as `ts_unreachable_check.cjs` reads it: a comment holding
#: only the marker. `#` too, so a Python module can carry one.
_GREP = (
    r"^[[:space:]]*(//|/?\*+|#)[[:space:]]*kept-for:[[:space:]]+[a-z][a-z0-9-]*-[0-9]+[[:space:]]+-"
)
_MARKER = re.compile(r"kept-for:\s+(?P<ticket>\S+)\s+-\s+(?P<reason>.+?)\s*(?:\*/)?\s*$")
_NOT_TESTS = (
    ":(exclude,glob)**/__tests__/**",
    ":(exclude,glob)**/tests/**",
    ":(exclude,glob)**/*.test.*",
)
_TIMEOUT_SECONDS = 20
#: `git grep` found nothing. Anything else non-zero is a failure, not an answer.
_NO_MATCH = 1
#: Markers named in a finding before it says "and N more".
_NAMED = 5


class KeptCodeUnreadable(Exception):
    """The checkout could not be searched, which is not the same as finding none."""


@dataclass(frozen=True)
class KeptCode:
    path: str
    line: int
    ticket: str
    reason: str


def kept_code(repo_root: Path) -> list[KeptCode]:
    """Every `kept-for` marker in the checkout's tracked files."""
    try:
        result = run_git(
            ["grep", "-nI", "-z", "-E", "-e", _GREP, "--", ".", *_NOT_TESTS],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KeptCodeUnreadable(f"{repo_root}: {exc}") from exc
    if result.returncode == _NO_MATCH:
        return []
    if result.returncode != 0:
        raise KeptCodeUnreadable(f"{repo_root}: {result.stderr.strip()}")
    found = []
    for row in result.stdout.splitlines():
        path, line, text = row.split("\0", 2)
        marker = _MARKER.search(text)
        if marker is not None:
            found.append(KeptCode(path, int(line), marker["ticket"], marker["reason"]))
    return found


def ticket_kept_code(session: Session, ticket: Ticket) -> dict[str, object]:
    """`kept_code` for a ticket's brief, with why it is empty when it could not be read."""
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return {"kept_code": []}
    try:
        markers = kept_code(resolve_workspace_root(workspace))
    except KeptCodeUnreadable as exc:
        return {"kept_code": [], "kept_code_error": str(exc)}
    return {
        "kept_code": [asdict(item) for item in markers if item.ticket == ticket.external_id],
    }


def _problem(session: Session, marker: KeptCode) -> str:
    """Why this marker no longer names work to come, or an empty string."""
    try:
        ticket = resolve_one(session, marker.ticket)
    except (TicketNotFound, AmbiguousTicketReference) as exc:
        return str(exc)
    if ticket.external_id != marker.ticket:
        return f"{marker.ticket} is now {ticket.external_id}"
    if ticket.state in StateMachine.TERMINAL_TICKET_STATES:
        return f"{marker.ticket} is {ticket.state.value}"
    return ""


def check_kept_code_tickets(
    session: Session, _workspace: Workspace, repo_root: Path
) -> DoctorFinding:
    """Does every `kept-for` marker still name a ticket someone will work?"""
    try:
        markers = kept_code(repo_root)
    except KeptCodeUnreadable as exc:
        return DoctorFinding(
            check=DoctorCheck.KEPT_CODE_TICKETS,
            status=DoctorStatus.WARN,
            finding=f"Could not search for kept-for markers: {exc}",
            remediation="Point the workspace's repo_path at a git checkout.",
        )
    stale = [(m, why) for m in markers if (why := _problem(session, m))]
    if not stale:
        return DoctorFinding(
            check=DoctorCheck.KEPT_CODE_TICKETS,
            status=DoctorStatus.PASS,
            finding=f"All {len(markers)} kept-for marker(s) name an open ticket.",
        )
    named = "; ".join(f"{m.path}:{m.line} ({why})" for m, why in stale[:_NAMED])
    more = f"; and {len(stale) - _NAMED} more" if len(stale) > _NAMED else ""
    return DoctorFinding(
        check=DoctorCheck.KEPT_CODE_TICKETS,
        status=DoctorStatus.WARN,
        finding=f"{len(stale)} unused module(s) are kept for work that is over: {named}{more}.",
        remediation=(
            "Delete the code and its tests, or name the open ticket that still needs it in the "
            "module's `kept-for:` comment."
        ),
    )
