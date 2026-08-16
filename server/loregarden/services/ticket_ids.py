"""Spelling and resolution of the shareable ticket id.

A ticket carries two identifiers. ``Ticket.id`` is a UUID and is what all 21 of
the schema's ticket foreign keys point at; it is never meant to be read aloud.
``Ticket.external_id`` is the one a human types, pastes into a message, or puts
in a branch name::

    lor-mcp-gateway-142
     │       │        └─ ticket_number — per workspace, monotonic, assigned once
     │       └─ the milestone ancestor's code
     └─ the workspace's ticket_prefix

Only the number is authoritative. :func:`resolve` matches on it whenever the
trailing integer parses, so ``lor-mcp-gateway-142``, ``lor-anything-142`` and
``lor-142`` all find the same ticket. That is what makes the milestone segment
safe to re-spell: a ticket that moves to another milestone gets a new
``external_id``, and every link shared under the old spelling keeps working.

A ticket with no milestone ancestor takes :data:`NO_MILESTONE_SEGMENT` in that
slot (``lor-none-142``) rather than dropping the segment, so every id has the
same shape and "filed under no milestone" is stated rather than inferred from a
missing part.
"""

from __future__ import annotations

import re

from loregarden.models.domain import Ticket, WorkItemType, Workspace
from sqlalchemy import func
from sqlalchemy.orm import InstrumentedAttribute
from sqlmodel import Session, col, select

#: Milestone segment for a ticket with no milestone ancestor. Reserved: a real
#: milestone can never derive this code (see :func:`derive_milestone_code`).
NO_MILESTONE_SEGMENT = "none"

#: Used when a workspace slug carries no usable letters at all.
FALLBACK_PREFIX = "ws"

_PREFIX_LENGTH = 3
_MILESTONE_CODE_WORDS = 2
_MILESTONE_CODE_MAX = 24

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_STRUCTURED_ID_RE = re.compile(
    r"^(?P<prefix>[a-z0-9]+)(?:-(?P<segment>[a-z0-9-]*?))?-(?P<number>\d+)$"
)

# Dropped from the head of a milestone title before its code is taken: they order
# milestones, they do not name them. "01_milestone_bootstrap" -> "bootstrap",
# "Track A — MCP Gateway" -> "mcp-gateway".
_MILESTONE_NOISE_WORDS = frozenset({"track", "milestone", "m"})
# An ordinal in any of the spellings these titles use: "01", "m01", "v2".
_ORDINAL_RE = re.compile(r"^[a-z]?\d+$")
# Dropped anywhere: they pad a code without narrowing it.
_MILESTONE_STOP_WORDS = frozenset({"the", "a", "an", "and", "of", "for", "to"})


def _words(text: str) -> list[str]:
    return [word for word in _NON_SLUG_RE.split(text.lower()) if word]


def derive_workspace_prefix(slug: str, *, taken: frozenset[str]) -> str:
    """A short, stable leading segment for ``slug``, unique against ``taken``.

    Deliberately dumb — the first few letters, not an acronym. The column is
    editable, so a workspace that wants ``lg`` over ``lor`` sets it; guessing
    cleverly here would only make the default harder to predict.
    """
    letters = _NON_SLUG_RE.sub("", slug.lower())
    if not letters:
        letters = FALLBACK_PREFIX

    for length in range(_PREFIX_LENGTH, max(len(letters), _PREFIX_LENGTH) + 1):
        candidate = letters[:length]
        if candidate not in taken:
            return candidate

    base = letters[:_PREFIX_LENGTH]
    suffix = 2
    while f"{base}{suffix}" in taken:
        suffix += 1
    return f"{base}{suffix}"


def derive_milestone_code(title: str, *, taken: frozenset[str]) -> str:
    """The segment a milestone's whole subtree carries, unique against ``taken``."""
    words = _words(title)
    while words and (words[0] in _MILESTONE_NOISE_WORDS or _ORDINAL_RE.match(words[0])):
        words = words[1:]
        # A lone ordinal after the noise word ("track a", "m 01") orders it too.
        if words and (len(words[0]) == 1 or _ORDINAL_RE.match(words[0])):
            words = words[1:]

    kept = [word for word in words if word not in _MILESTONE_STOP_WORDS]
    code = "-".join((kept or words)[:_MILESTONE_CODE_WORDS])[:_MILESTONE_CODE_MAX].strip("-")
    if not code or code == NO_MILESTONE_SEGMENT:
        code = "milestone"

    if code not in taken:
        return code
    suffix = 2
    while f"{code}-{suffix}" in taken:
        suffix += 1
    return f"{code}-{suffix}"


def spell_external_id(*, prefix: str, milestone_code: str, number: int) -> str:
    """Assemble the three parts into the id a human sees."""
    return "-".join(
        (
            prefix.strip() or FALLBACK_PREFIX,
            milestone_code.strip() or NO_MILESTONE_SEGMENT,
            str(number),
        )
    )


def parse_ticket_number(value: str) -> int | None:
    """The trailing integer of a structured id, or None if ``value`` is not one."""
    match = _STRUCTURED_ID_RE.match(value.strip().lower())
    return int(match.group("number")) if match else None


def parse_workspace_prefix(value: str) -> str | None:
    """The leading segment of a structured id, or None if ``value`` is not one."""
    match = _STRUCTURED_ID_RE.match(value.strip().lower())
    return match.group("prefix") if match else None


def workspace_prefix(session: Session, workspace: Workspace) -> str:
    """``workspace.ticket_prefix``, deriving and persisting one if it is unset."""
    if workspace.ticket_prefix.strip():
        return workspace.ticket_prefix.strip()

    taken = frozenset(
        prefix
        for prefix in session.exec(
            select(Workspace.ticket_prefix).where(Workspace.id != workspace.id)
        ).all()
        if prefix
    )
    workspace.ticket_prefix = derive_workspace_prefix(workspace.slug, taken=taken)
    session.add(workspace)
    return workspace.ticket_prefix


def milestone_code(session: Session, milestone: Ticket) -> str:
    """``milestone.milestone_code``, deriving and persisting one if it is unset."""
    if milestone.milestone_code.strip():
        return milestone.milestone_code.strip()

    taken = frozenset(
        code
        for code in session.exec(
            select(Ticket.milestone_code).where(
                Ticket.workspace_id == milestone.workspace_id,
                Ticket.id != milestone.id,
            )
        ).all()
        if code
    )
    milestone.milestone_code = derive_milestone_code(milestone.title, taken=taken)
    session.add(milestone)
    return milestone.milestone_code


def ancestor_milestone(session: Session, ticket: Ticket) -> Ticket | None:
    """The milestone ``ticket`` sits under, or itself if it is one.

    Walks by id rather than trusting ``Ticket.milestone`` — that column is free
    text, empty on most rows, and inconsistent where it is set.
    """
    if ticket.work_item_type == WorkItemType.MILESTONE:
        return ticket

    seen = {ticket.id}
    current = ticket
    while current.parent_ticket_id:
        parent = session.get(Ticket, current.parent_ticket_id)
        # A missing parent or a cycle means the chain cannot name a milestone;
        # NO_MILESTONE_SEGMENT is the honest answer, not an exception.
        if parent is None or parent.id in seen:
            return None
        if parent.work_item_type == WorkItemType.MILESTONE:
            return parent
        seen.add(parent.id)
        current = parent
    return None


def milestone_segment_for(session: Session, ticket: Ticket) -> str:
    """The milestone code ``ticket``'s id should carry."""
    milestone = ancestor_milestone(session, ticket)
    return milestone_code(session, milestone) if milestone else NO_MILESTONE_SEGMENT


def next_ticket_number(session: Session, workspace: Workspace) -> int:
    """One past the highest number ever issued in this workspace, and record it.

    The high-water mark lives on the workspace, not in ``max(ticket_number)``:
    deleting the newest ticket would lower that maximum and hand its number —
    and every link anyone shared to it — straight to the next ticket created.
    The live maximum is still consulted, so a database that predates the
    counter, or one whose rows were edited underneath it, cannot issue a
    duplicate.
    """
    highest_live = session.exec(
        select(func.max(Ticket.ticket_number)).where(Ticket.workspace_id == workspace.id)
    ).one()
    issued = max(workspace.last_ticket_number, int(highest_live or 0)) + 1
    workspace.last_ticket_number = issued
    session.add(workspace)
    return issued


def resolve(session: Session, value: str, *, workspace_id: str | None = None) -> Ticket | None:
    """Find a ticket by any spelling of its external id.

    Tried in order: the current id, the pre-restructure id, then the ticket
    number carried by a structured id whose prefix names a workspace. The last
    step is what keeps a shared link alive across a re-parent.

    The number step is gated on the prefix matching a real workspace, including
    when the workspace is already known. Plenty of ids that are not ours end in
    a number — ``PRIM-1``, ``test-sib-2`` — and treating those as ticket 1 and
    ticket 2 would hand back a confidently wrong ticket where returning nothing
    is correct.
    """
    ref = value.strip()
    if not ref:
        return None

    def by_spelling(column: InstrumentedAttribute[str]) -> Ticket | None:
        query = select(Ticket).where(column.in_([ref, ref.lower()]))
        if workspace_id:
            query = query.where(Ticket.workspace_id == workspace_id)
        return session.exec(query).first()

    ticket = by_spelling(col(Ticket.external_id)) or by_spelling(col(Ticket.legacy_external_id))
    if ticket:
        return ticket

    lowered = ref.lower()
    workspace = session.get(Workspace, workspace_id) if workspace_id else None
    # A bare number only identifies a ticket once a workspace is known; without
    # one it would resolve to a different ticket in each workspace.
    if workspace is not None and lowered.isdigit():
        number: int | None = int(lowered)
    else:
        number = parse_ticket_number(lowered)
        prefix = parse_workspace_prefix(lowered)
        if workspace is None:
            workspace = session.exec(
                select(Workspace).where(Workspace.ticket_prefix == prefix)
            ).first()
        elif workspace.ticket_prefix != prefix:
            return None

    if number is None or workspace is None:
        return None

    return session.exec(
        select(Ticket).where(
            Ticket.workspace_id == workspace.id,
            Ticket.ticket_number == number,
        )
    ).first()


def assign_external_id(
    session: Session, ticket: Ticket, workspace: Workspace, *, supplied_id: str = ""
) -> str:
    """Issue ``ticket`` its number and spell its id. Call once, at creation.

    ``supplied_id`` is an id from somewhere else — an import's key, a proposal's
    ref, a seed literal. It is recorded as the legacy id, where it stays
    resolvable, rather than becoming the ticket's id: every ticket this system
    creates gets an id this system spelled, or the shareable form would be
    optional in exactly the paths that create tickets in bulk.
    """
    if supplied_id.strip():
        ticket.legacy_external_id = supplied_id.strip()
    if not ticket.ticket_number:
        ticket.ticket_number = next_ticket_number(session, workspace)
    ticket.external_id = spell_external_id(
        prefix=workspace_prefix(session, workspace),
        milestone_code=milestone_segment_for(session, ticket),
        number=ticket.ticket_number,
    )
    return ticket.external_id


def reissue_in_workspace(
    session: Session, tickets: list[Ticket], workspace: Workspace
) -> list[Ticket]:
    """Renumber tickets that have just changed workspace.

    ``ticket_number`` is unique per workspace, so a moved ticket cannot keep its
    number — it would collide with whatever already holds it in the destination
    and make ``lor-142`` ambiguous. Each moved ticket takes a fresh number and
    records the id it arrived under in ``legacy_external_id``, so the id used in
    the source workspace still resolves.

    ``tickets`` must be parent-before-child: a milestone's code is re-derived
    against the destination's codes, and its descendants read it back.
    """
    for ticket in tickets:
        ticket.ticket_number = 0
        if ticket.work_item_type == WorkItemType.MILESTONE:
            ticket.milestone_code = ""
        session.add(ticket)

    for ticket in tickets:
        # Only when blank: an earlier legacy id is the one written into branches
        # and artifacts years ago, and one column can hold one of them.
        if not ticket.legacy_external_id.strip():
            ticket.legacy_external_id = ticket.external_id
        assign_external_id(session, ticket, workspace)
        session.add(ticket)
    return tickets


def respell_external_id(session: Session, ticket: Ticket, workspace: Workspace) -> str:
    """Re-derive the milestone segment for a ticket that has moved parents.

    Nothing re-parents a ticket inside a workspace today — the only move that
    exists crosses workspaces and goes through :func:`reissue_in_workspace`. This
    is here for the write that adds one, and is the reason the number, not the
    spelling, is what :func:`resolve` matches on: an id shared before the move
    keeps working, so nothing has to record the old spelling.
    """
    ticket.external_id = spell_external_id(
        prefix=workspace_prefix(session, workspace),
        milestone_code=milestone_segment_for(session, ticket),
        number=ticket.ticket_number,
    )
    return ticket.external_id
