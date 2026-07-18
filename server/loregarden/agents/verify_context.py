"""What a verifier is shown: the claim under review, and the change behind it.

A stage closes on its own `outcome=pass`. The verify stage exists so that claim
is checked by something that did not make it — which only works if the verifier
is kept ignorant of the reasoning that produced it. This module assembles the
evidence a skeptic needs; `_build_prompt` withholds the inherited context a
skeptic must not have.
"""

from __future__ import annotations

import json
import logging

from loregarden.models.domain import Artifact, Ticket, Workspace
from loregarden.services.artifact_service import ensure_diff_artifact
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

MAX_VERIFY_CHARS = 8000
_MAX_DIFF_SECTIONS = 12


def _latest_stage_claim(session: Session, ticket: Ticket) -> dict | None:
    """The most recent stage report on this ticket — the claim being verified."""
    rows = session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id, Artifact.kind == "context")
        .order_by(Artifact.created_at.desc())
    ).all()
    for row in rows:
        try:
            content = json.loads(row.content_json or "{}")
        except (TypeError, ValueError):
            continue
        # Stage reports are the context artifacts carrying a status verdict.
        if content.get("stage_key") and content.get("status"):
            return content
    return None


def _diff_lines(session: Session, ticket: Ticket, workspace: Workspace) -> list[str]:
    diff = ensure_diff_artifact(session, ticket=ticket, workspace=workspace)
    if not diff:
        return []
    lines = [f"Summary: {diff.get('summary') or diff.get('file') or 'changes'}"]
    sections = diff.get("sections") or []
    for section in sections[:_MAX_DIFF_SECTIONS]:
        path = section.get("file") or section.get("path") or "?"
        body = section.get("body") or section.get("patch") or ""
        lines += ["", f"--- {path}", body]
    if len(sections) > _MAX_DIFF_SECTIONS:
        lines.append(f"\n(+{len(sections) - _MAX_DIFF_SECTIONS} more files not shown)")
    return lines


def build_verify_context(
    session: Session,
    ticket: Ticket,
    workspace: Workspace,
    *,
    max_chars: int = MAX_VERIFY_CHARS,
) -> str:
    """The claim and the change, or "" when there is nothing to verify.

    Never raises: a verifier that cannot read the diff should say so in its
    verdict rather than take the whole run down.
    """
    try:
        claim = _latest_stage_claim(session, ticket)
        diff_lines = _diff_lines(session, ticket, workspace)
    except Exception:  # noqa: BLE001 - a broken read must not fail the run
        logger.warning("Verify context unavailable for ticket %s", ticket.id, exc_info=True)
        return ""

    if not claim and not diff_lines:
        return ""

    lines = [
        "You are checking a claim you did not make. Confirm it only against what",
        "you can observe by running the code — not because it reads plausibly.",
    ]
    if claim:
        lines += [
            "",
            "### The claim",
            f"- stage: {claim.get('stage_key')}",
            f"- reported: {claim.get('status')} (confidence {claim.get('confidence')})",
        ]
        if claim.get("reroute_context"):
            lines.append(f"- notes: {claim['reroute_context']}")
    if diff_lines:
        lines += ["", "### The change it claims to have made", *diff_lines]
    return "\n".join(lines)[:max_chars]
