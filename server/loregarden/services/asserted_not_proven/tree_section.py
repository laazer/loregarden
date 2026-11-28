"""The "Asserted-not-proven" section of a tree's PR.

Modelled on `services.tree_fix_section` and called from `services.publish_tree`
beside it. What reaches the PR reader is what this tree still believes without
proof: the statement, the half proven today, the half owed, and the fallback
named as not built.

The two halves are rendered as SEPARATE LABELLED LINES, never joined into one
sentence. Anything citing the guarantee cites the split, and a rendering that
concatenates them hands the reader back the undivided guarantee AC5 exists to
refuse.

An empty class renders nothing at all — not an empty heading. An always-present
heading with nothing under it trains readers to skip the section.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.models.domain import AssertedNotProvenKind, Ticket
from loregarden.services.asserted_not_proven.listing import (
    RECORD_TOOL,
    Member,
    members_for_tickets,
)
from loregarden.services.hierarchy_service import descendants_by_root
from sqlmodel import Session

SECTION_HEADING = "## Asserted-not-proven"


def _lines(member: Member) -> list[str]:
    claim = member.claim
    out = [f"#### {claim.claim}", ""]
    if claim.proven_today:
        out.append(f"- **Proven today:** {claim.proven_today}")
    out.append(f"- **Owed:** `{claim.owed_artifact}`")
    if claim.owed_description:
        out.append(f"- **Owed, described:** {claim.owed_description}")
    if claim.owed_environment:
        out.append(f"- **Needs environment:** {claim.owed_environment}")
        out.append(f"- **Portable subset, landed now:** {claim.portable_subset}")
    out.append(f"- **Fallback:** {claim.fallback}")
    if claim.member_kind is AssertedNotProvenKind.CODE_SITE:
        out.append(f"- **Ceiling:** {claim.ceiling}")
        out.append(f"- **Upgrade path:** {claim.upgrade_path}")
    if claim.challenger:
        out.append(f"- **Frozen, challenged by:** {claim.challenger}")
    if claim.routed_ticket:
        out.append(f"- **Routed to:** {claim.routed_ticket}")
    if member.unpaid_after_landing:
        out.append("- **Unpaid after landing:** this ticket is done and its proof never appeared")
    out.append(f"- **Qualifies because:** {claim.qualifies_because}")
    out.append(f"- **Upstream:** {claim.upstream_id} — **cited:** {claim.citation}")
    out.append("")
    return out


def asserted_not_proven_section(
    session: Session,
    root: Ticket,
    *,
    branch: str,
    repo_root: Path | None,
) -> str:
    """The section for the claims open on the tickets landed on ``branch``; "" for none.

    The root counts as itself: its own commits are on the branch.
    """
    landed = [
        ticket
        for ticket in descendants_by_root(session, [root.id])[root.id]
        if ticket.landed_branch == branch and ticket.landed_sha
    ]
    tickets = [root, *landed]
    members = members_for_tickets(session, tickets, repo_root=repo_root)
    if not members:
        return ""
    lines = [
        "",
        SECTION_HEADING,
        "",
        "_What this tree believes without proof, each naming the artifact it is owed and "
        f"the fallback if it turns out false (`{RECORD_TOOL}`)._",
        "",
    ]
    for member in members:
        lines += _lines(member)
    return "\n".join(lines)
