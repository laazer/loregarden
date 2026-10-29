"""The "Fix coverage" section of a tree's PR, assembled from its bug tickets' records.

A tree's PR is titled `<external_id>: <title>`, never `fix:`, so the "PR fix
coverage" check only reads its section when the section is there. This puts it
there whenever the publish carries a bug ticket, one `####` block per ticket,
so the check sees each bug's answers (`services.fix_coverage`) and proves the
invariant and the prevention test they name exist in the merged tree.

A bug with no record gets a block naming the tool and no answers, which the
check fails. That is deliberate: "this fix leaves no state behind" is a claim
only the ticket can make, never a waiver written here.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket
from loregarden.services.fix_coverage import RECORD_TOOL, latest_fix_coverage, needs_fix_coverage
from loregarden.services.hierarchy_service import descendants_by_root
from sqlmodel import Session

SECTION_HEADING = "## Fix coverage"


def _block(session: Session, ticket: Ticket) -> list[str]:
    label = ticket.external_id or ticket.id[:8]
    head = [f"#### {label}: {ticket.title}", ""]
    try:
        coverage = latest_fix_coverage(session, ticket)
    except ValueError as exc:
        return [*head, f"_{exc} Record it again with `{RECORD_TOOL}` for `{label}`._", ""]
    if coverage is None:
        return [
            *head,
            f"_No fix coverage recorded. Record it with `{RECORD_TOOL}` for `{label}`; "
            "the next publish fills this in._",
            "",
        ]
    return [*head, *coverage.lines, ""]


def tree_fix_section(session: Session, root: Ticket, *, branch: str) -> str:
    """The section for the bug tickets landed on ``branch`` under ``root``; "" for none.

    The root counts when it is a bug itself: its own commits are on the branch.
    """
    landed = [
        ticket
        for ticket in descendants_by_root(session, [root.id])[root.id]
        if ticket.landed_branch == branch and ticket.landed_sha
    ]
    bugs = [ticket for ticket in [root, *landed] if needs_fix_coverage(ticket)]
    if not bugs:
        return ""
    lines = [
        "",
        SECTION_HEADING,
        "",
        f"_Assembled by Loregarden from the fix coverage each bug recorded (`{RECORD_TOOL}`)._",
        "",
    ]
    for ticket in bugs:
        lines += _block(session, ticket)
    return "\n".join(lines)
