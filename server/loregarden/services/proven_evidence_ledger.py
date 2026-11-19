"""A missing-evidence name stays open only while the evidence is missing.

`stage_verdict` raises `evidence:<kind> missing` when the stage's
`required_evidence` has no proof at the ticket's HEAD, and `unresolved_ledger`
persists it. It used to be clearable only by the agent that raised it, so a
name a repair turn raised outlived the implementer's proof: every later pass
was held as needs_rework over evidence sitting at HEAD, until the loop cap
blocked the ticket (lg-code-knowledge-1084 ran three such rounds of 50-105
minutes each). `unresolved_ledger.clear` now binds the name to its stage; this
invariant repairs the names that went stale before that, and reports any that
still do.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from loregarden.models.domain import InvariantReentry, Ticket
from loregarden.services.evidence import evidence_for_commit, resolve_head_sha
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.unresolved_ledger import (
    LedgerActor,
    UnresolvedOpenItem,
    clear,
    open_items,
    tickets_with_open_names,
)
from sqlmodel import Session

#: The ledger actor a repair clears as; never an agent, never `human`.
RECONCILER_AGENT_ID = "reconciler"

_MISSING = re.compile(r"^evidence:(?P<kind>.+) missing$")


def _proven_names(session: Session, ticket: Ticket) -> list[tuple[UnresolvedOpenItem, Violation]]:
    """Open missing-evidence names whose evidence is attached at the ticket's HEAD."""
    found: list[tuple[UnresolvedOpenItem, Violation]] = []
    head = ""
    for item in open_items(session, ticket):
        match = _MISSING.match(item.name)
        if match is None or not item.producer_stage_key:
            continue
        head = head or resolve_head_sha(session, ticket)
        if not head:
            return found
        proof = evidence_for_commit(session, ticket, commit_sha=head, evidence_kind=match["kind"])
        if not proof:
            continue
        found.append(
            (
                item,
                Violation(
                    subject_id=f"{ticket.id}:{item.name}",
                    ticket_id=ticket.id,
                    entered_at=min(row.created_at for row in proof),
                    detail=(
                        f"{ticket.external_id} holds {item.name!r} open (raised by "
                        f"{item.producer_agent_id} at {item.producer_stage_key}) while "
                        f"{len(proof)} {match['kind']} evidence row(s) sit at HEAD {head[:10]}"
                    ),
                ),
            )
        )
    return found


def proven_evidence_held_open(session: Session) -> list[Violation]:
    found: list[Violation] = []
    for ticket_id in tickets_with_open_names(session):
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            continue
        found.extend(violation for _, violation in _proven_names(session, ticket))
    return found


def clear_proven_evidence(session: Session, violations: Sequence[Violation]) -> int:
    """Clear each name as its own stage would, re-checking the proof first."""
    repaired = 0
    for ticket_id in sorted({v.ticket_id for v in violations if v.ticket_id}):
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            continue
        for item, _ in _proven_names(session, ticket):
            actor = LedgerActor(agent_id=RECONCILER_AGENT_ID, stage_key=item.producer_stage_key)
            repaired += int(clear(session, ticket, item.name, actor))
    return repaired


PROVEN_EVIDENCE_IS_NOT_HELD_OPEN = Invariant(
    name="proven_evidence_is_not_held_open",
    ticket="lg-code-knowledge-1084",
    statement=(
        "no unresolved_open snapshot holds an 'evidence:<kind> missing' name while "
        "evidence of that kind is attached at the ticket's HEAD"
    ),
    detect=proven_evidence_held_open,
    repair=clear_proven_evidence,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 19),
    prevented_by=(
        "server/tests/test_unresolved_ledger.py"
        "::test_missing_evidence_is_cleared_by_whoever_runs_its_stage"
    ),
)
