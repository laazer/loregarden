"""Which recorded landings a land-verification receipt covers.

`land_verification_ran`'s detector reads this; the receipt words are
`land_verification`'s, and this module is the one reading of them.

A landing is covered by either of two things:

- **A passing receipt.** A suite ran against the candidate merge and passed.
- **A contained landing from a ticket that wrote nothing.** The target already
  held the branch (`outcome == already_contained`), so `merge_tips` built no
  candidate and no suite could run. When every run the ticket had recorded
  its changed paths and every record is empty, the landing moved no code of
  this ticket's onto anything: there was nothing to verify, which is a
  different fact from "nobody verified it". Measured on the live database
  2026-11-15: 274 of `land_verification_ran`'s 345 violations were contained
  landings on `integration/lg-reference-sources-358`, every tip a "Refresh …
  from main" commit, and 270 of them survey carriers whose runs all recorded
  `[]`.

A contained landing whose runs wrote code, or whose runs include one with no
record, stays uncovered: the work reached the target some other way, and a
person has to find out whether that way was verified. A run with no record
cannot say it wrote nothing (`recorded_paths`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from loregarden.models.domain import Artifact, ArtifactKind, Ticket
from loregarden.services.land_verification import RECEIPT_ALREADY_CONTAINED, RECEIPT_PASSED
from loregarden.services.recorded_paths import tickets_that_recorded_writing_nothing
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session, col, select


class _ReceiptVerdict(BaseModel):
    """The two fields of a receipt body this module reads."""

    model_config = ConfigDict(extra="ignore")

    status: str = ""
    outcome: str = ""


def _verdict(body: str | None) -> _ReceiptVerdict | None:
    """A receipt body's verdict, or None when it cannot be read.

    `content_json` is a text column any `loregarden_attach_artifact` caller and
    any hand repair can write. A body that cannot be read is no verdict, and it
    must not raise: the detector is one step of the reconcile pass, and one that
    raises reports nothing for any ticket while the surface says "no violations".
    """
    try:
        return _ReceiptVerdict.model_validate(json.loads(body or ""))
    except (ValueError, ValidationError):
        return None


def _covers(verdicts: Sequence[_ReceiptVerdict], *, wrote_nothing: bool) -> bool:
    """Whether these receipts cover the landing.

    The comparisons are exact: the writer controls these words, and the
    client's `PASSING` set matches them exactly.
    """
    if any(verdict.status == RECEIPT_PASSED for verdict in verdicts):
        return True
    return wrote_nothing and any(
        verdict.outcome == RECEIPT_ALREADY_CONTAINED for verdict in verdicts
    )


def covered_landings(session: Session, tickets: Sequence[Ticket]) -> set[str]:
    """The ids of these tickets whose recorded landing a receipt covers."""
    ticket_ids = [ticket.id for ticket in tickets]
    if not ticket_ids:
        return set()
    verdicts: dict[str, list[_ReceiptVerdict]] = {}
    for ticket_id, body in session.exec(
        select(col(Artifact.ticket_id), col(Artifact.content_json)).where(
            col(Artifact.kind) == ArtifactKind.LAND_VERIFICATION,
            col(Artifact.ticket_id).in_(ticket_ids),
        )
    ).all():
        verdict = _verdict(body)
        if verdict is not None:
            verdicts.setdefault(ticket_id, []).append(verdict)
    wrote_nothing = tickets_that_recorded_writing_nothing(session, list(verdicts))
    return {
        ticket_id
        for ticket_id, found in verdicts.items()
        if _covers(found, wrote_nothing=ticket_id in wrote_nothing)
    }
