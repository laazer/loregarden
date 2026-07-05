"""Git branch helpers for ticket-scoped agent runs."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from loregarden.models.domain import Ticket

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def default_ticket_branch(ticket: Ticket) -> str:
    slug = ticket.external_id.strip() or ticket.id[:8]
    return f"loregarden/{slug}"


def resolve_ticket_branch(ticket: Ticket) -> str:
    branch = ticket.branch.strip()
    return branch or default_ticket_branch(ticket)


def validate_branch_name(branch: str) -> None:
    if not branch or not _BRANCH_RE.match(branch):
        raise ValueError(f"Invalid branch name: {branch!r}")


def ensure_ticket_branch(repo_root: Path, ticket: Ticket) -> str:
    """Checkout or create the branch a ticket should run on."""
    branch = resolve_ticket_branch(ticket)
    validate_branch_name(branch)

    if not (repo_root / ".git").exists():
        raise ValueError(f"Workspace repo is not a git repository: {repo_root}")

    subprocess.run(
        ["git", "checkout", "-B", branch],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return branch
