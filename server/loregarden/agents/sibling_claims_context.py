"""Tell a stage which sibling tickets claim the files it is touching.

The warning goes in the prompt rather than a hook, so every adapter sees it:
hooks reach only the Claude CLI, and about one run in eight is on another.
It is short on purpose: a few siblings and a few paths each, because this
block is sent to every stage of every ticket that overlaps.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import Ticket
from loregarden.models.domain.path_claim_enums import PathClaimSource
from loregarden.services.path_claims import SiblingOverlap, sibling_overlaps
from sqlmodel import Session

logger = logging.getLogger(__name__)

HEADING = "## Sibling tickets claiming the same files"
MAX_SIBLINGS = 6
MAX_PATHS_PER_SIBLING = 4

_INTRO = (
    "These open tickets land on the same integration branch as this one, and claim "
    "files this ticket also touches. Overlapping edits conflict when the second of "
    "you lands."
)
_GUIDANCE = (
    "In these files, keep your diff to what this ticket needs: no reformatting, "
    "reordering or renames nobody asked for, and do not edit the sibling's work. "
    "Where new code could reasonably live in a new function or module, put it there."
)


def build_sibling_claims_context(session: Session, ticket: Ticket) -> str:
    """The block body, or "" when no open sibling claims a file this ticket claims.

    Never raises: the block is advice, and a run that cannot have it should run
    without it rather than fail. A failure is logged with its traceback.
    """
    try:
        overlaps = sibling_overlaps(session, ticket)
    except Exception:  # noqa: BLE001 - silent-ok: logged with traceback; the run proceeds without advisory context it never depended on
        logger.exception("sibling file claims unavailable for %s", ticket.external_id)
        return ""
    if not overlaps:
        return ""
    lines = [_INTRO, ""]
    lines += [_describe(overlap) for overlap in overlaps[:MAX_SIBLINGS]]
    if len(overlaps) > MAX_SIBLINGS:
        lines.append(f"- and {len(overlaps) - MAX_SIBLINGS} more sibling tickets")
    lines += ["", _GUIDANCE]
    return "\n".join(lines)


def _describe(overlap: SiblingOverlap) -> str:
    sibling = overlap.sibling
    where = sibling.workflow_stage_key or sibling.state.value
    parts = [
        f"{verb} {_paths([c.path for c in overlap.shared if c.source is source])}"
        for source, verb in (
            (PathClaimSource.OBSERVED, "has changed"),
            (PathClaimSource.PLANNED, "plans to change"),
        )
        if any(c.source is source for c in overlap.shared)
    ]
    return f"- {sibling.external_id} ({where}): {'; '.join(parts)}"


def _paths(paths: list[str]) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:MAX_PATHS_PER_SIBLING])
    extra = len(paths) - MAX_PATHS_PER_SIBLING
    return f"{shown} and {extra} more" if extra > 0 else shown
