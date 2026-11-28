"""Writing the class: record, amend, measure, close.

Four operations over one identity, `(ticket_id, claim_slug)`, stored as
`artifacts` rows under `ArtifactKind.ASSERTED_NOT_PROVEN` with the slug as the
row's title. Three properties this module exists to hold, each of which the
obvious implementation gets wrong in the one direction the class cannot
survive — **a count that lies**:

- **Idempotency.** A write whose payload equals the *latest* stored payload for
  that identity writes nothing and returns the existing row. A retried MCP call
  is the normal failure here, and the count is the feature.
- **History.** A changed payload is a NEW row carrying `amends`, never an
  in-place rewrite: AC4 says the class grows by amendment, each member joining
  with its qualifying reason, so the superseded reasons have to survive. Every
  count is over distinct identities, so history never inflates the total.
- **The AC8 refusal.** `amend_claim` and `close_claim` RAISE unless every
  `dependent_documents` entry is answered, with the edit that was made or a
  ticket external id that resolves. Not a warning and not a log line: updating a
  dependent document is an implementation obligation tracked as work, and a
  warning in an unattended run is a failure nobody sees.

The identity is a digest-suffixed slug rather than a plain slugification. A
folding slug makes "the reaper keeps dirty checkouts" and "The reaper keeps
dirty checkouts!" one member, and a truncating one makes every long statement
the same statement — both lie downward, and the first write always looks right.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass

from loregarden.models.domain import (
    Artifact,
    ArtifactKind,
    AssertedNotProvenKind,
    ClaimOutcome,
    Ticket,
)
from loregarden.services.asserted_not_proven.schemas import (
    AssertedNotProvenClaim,
    parse_claim,
)
from loregarden.services.fix_coverage import MIN_ANSWER
from loregarden.services.ticket_ids import (
    AmbiguousTicketReference,
    TicketNotFound,
    resolve_one,
)
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

#: How much of the statement stays readable in the artifact title.
_SLUG_PREFIX = 60
#: Enough digest that two distinct statements do not share an identity, short
#: enough that the title still reads as the claim it names.
_SLUG_DIGEST = 12
_UNSLUGGABLE = re.compile(r"[^a-z0-9]+")


def claim_slug(statement: str) -> str:
    """The identity of ``statement`` within its ticket.

    Readable prefix plus a digest of the WHOLE statement. The prefix is for the
    person reading `artifacts.title`; the digest is what keeps two statements
    that differ only in case, punctuation or their last word from silently
    becoming one member.
    """
    digest = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:_SLUG_DIGEST]
    readable = _UNSLUGGABLE.sub("-", statement.lower()).strip("-")[:_SLUG_PREFIX].strip("-")
    return f"{readable}-{digest}" if readable else digest


class ClaimLifecycle(BaseModel):
    """What a row records beyond the claim itself.

    Serialized with `exclude_defaults`, so a freshly recorded claim's payload is
    exactly its claim dump and nothing else — which is what lets a reader round
    trip it, and what keeps the idempotency digest stable.

    No `measured_at`: the row's own `created_at` is when the measurement was
    taken, and a timestamp inside the payload would make a retried identical
    measurement a new row for no reader's benefit.
    """

    model_config = ConfigDict(extra="forbid")

    #: The row this one supersedes. Newest wins on read.
    amends: str = ""
    outcome: ClaimOutcome | None = None
    evidence: str = ""
    measured_value: float | None = None
    measured_unit: str = ""


@dataclass(frozen=True)
class Written:
    """What a write did. ``amended`` is False for a no-op and for a first write."""

    artifact_id: str
    amended: bool


@dataclass(frozen=True)
class StoredClaim:
    """The latest row for one identity, split back into its two halves."""

    artifact_id: str
    slug: str
    claim: AssertedNotProvenClaim
    lifecycle: ClaimLifecycle

    @property
    def settled(self) -> bool:
        return self.lifecycle.outcome is not None


def to_payload(claim: AssertedNotProvenClaim, lifecycle: ClaimLifecycle) -> dict:
    return {
        **claim.model_dump(mode="json"),
        **lifecycle.model_dump(mode="json", exclude_defaults=True),
    }


def from_payload(payload: dict) -> tuple[AssertedNotProvenClaim, ClaimLifecycle]:
    """Split a stored payload, raising ``ValueError`` when it no longer validates.

    This module wrote the row, so one that fails is a defect, not an answer.
    """
    lifecycle_keys = set(ClaimLifecycle.model_fields)
    lifecycle = ClaimLifecycle.model_validate(
        {key: value for key, value in payload.items() if key in lifecycle_keys}
    )
    claim = parse_claim({key: value for key, value in payload.items() if key not in lifecycle_keys})
    return claim, lifecycle


def _rows(session: Session, ticket: Ticket, slug: str) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact)
            .where(
                Artifact.ticket_id == ticket.id,
                Artifact.kind == ArtifactKind.ASSERTED_NOT_PROVEN,
                Artifact.title == slug,
            )
            .order_by(col(Artifact.created_at).asc())
        ).all()
    )


def latest_row(session: Session, ticket: Ticket, slug: str) -> Artifact | None:
    """The row for ``slug`` that nothing else supersedes.

    Walked through the `amends` chain rather than taken as the newest
    `created_at`: two rows written inside the same clock tick would otherwise
    order arbitrarily, and the identity's current text is not a coin toss.
    """
    rows = _rows(session, ticket, slug)
    if not rows:
        return None
    superseded = {json.loads(row.content_json or "{}").get("amends", "") for row in rows} - {""}
    live = [row for row in rows if row.id not in superseded]
    # A chain always has exactly one tip; more than one means two writers raced
    # the same identity, and the newest is the honest answer rather than a crash.
    return live[-1] if live else rows[-1]


def latest_claim(session: Session, ticket: Ticket, slug: str) -> StoredClaim | None:
    row = latest_row(session, ticket, slug)
    if row is None:
        return None
    try:
        claim, lifecycle = from_payload(json.loads(row.content_json or "{}"))
    except ValueError as exc:
        raise ValueError(
            f"{ticket.external_id}'s claim {slug!r} ({row.id}) no longer validates: {exc}"
        ) from exc
    return StoredClaim(artifact_id=row.id, slug=slug, claim=claim, lifecycle=lifecycle)


def _write(session: Session, ticket: Ticket, slug: str, payload: dict) -> Artifact:
    artifact = Artifact(
        ticket_id=ticket.id,
        kind=ArtifactKind.ASSERTED_NOT_PROVEN,
        title=slug,
        content_json=json.dumps(payload),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def _resolution_checked(session: Session, ticket: Ticket, claim: AssertedNotProvenClaim) -> None:
    """The half of R9.4 the bare model cannot do: does the routed ticket exist.

    `resolve_one` performs I/O, so the model validates shape and this validates
    resolution. Both refusals exist, or one of them is the only one anyone
    relies on.
    """
    if claim.member_kind is not AssertedNotProvenKind.DOCUMENT_DEFECT:
        return
    try:
        resolve_one(session, claim.routed_ticket)
    except (TicketNotFound, AmbiguousTicketReference) as exc:
        raise ValueError(
            f"routed_ticket {claim.routed_ticket!r} does not resolve to one ticket: {exc}"
        ) from exc


def _obligations_met(
    session: Session,
    stored: StoredClaim,
    answers: dict[str, str],
) -> None:
    """AC8. Every dependent document is answered, or this raises naming them all.

    Naming all of them rather than the first: a refusal naming one of three
    costs the author three round trips.
    """
    unanswered: list[str] = []
    for document in stored.claim.dependent_documents:
        answer = (answers.get(document) or "").strip()
        if not answer or len(answer) < MIN_ANSWER:
            unanswered.append(f"{document} (no answer, or under {MIN_ANSWER} characters)")
            continue
        if _reads_as_ticket(answer):
            why = _unresolved(session, answer)
            if why:
                unanswered.append(f"{document} (ticket {answer!r}: {why})")
    if unanswered:
        raise ValueError(
            "updating a dependent document is an implementation obligation tracked as work, "
            "not a convention: answer each with the edit that was made, or the external id of "
            "a ticket that was filed. Unanswered: " + "; ".join(unanswered)
        )


def _reads_as_ticket(answer: str) -> bool:
    """An answer that is one bare token with no space is offered as a ticket id."""
    return " " not in answer


def _unresolved(session: Session, answer: str) -> str:
    """Why ``answer`` does not name exactly one ticket, or "" when it does.

    The reason is carried out of the exception rather than collapsed to a
    boolean: "no such ticket" and "three tickets carry that spelling" send the
    author somewhere different, and the refusal above prints whichever it was.
    """
    try:
        resolve_one(session, answer)
    except (TicketNotFound, AmbiguousTicketReference) as exc:
        return str(exc) or f"{answer!r} does not resolve to one ticket"
    return ""


def register_claim(
    session: Session,
    ticket: Ticket,
    claim: AssertedNotProvenClaim,
) -> Written:
    """File a claim. Identical to the stored one: a no-op, and it says so.

    A *changed* payload is refused rather than silently amended. Amendment is
    where AC8's dependent-document obligation is enforced, so a record that
    quietly amended would be the way around it.
    """
    _resolution_checked(session, ticket, claim)
    slug = claim_slug(claim.claim)
    payload = to_payload(claim, ClaimLifecycle())
    stored = latest_claim(session, ticket, slug)
    if stored is None:
        return Written(artifact_id=_write(session, ticket, slug, payload).id, amended=False)
    if to_payload(stored.claim, stored.lifecycle) == payload:
        return Written(artifact_id=stored.artifact_id, amended=False)
    raise ValueError(
        f"{ticket.external_id} already carries the claim {slug!r} with different text: change "
        "it with the 'amend' operation, which answers the dependent documents the change obliges"
    )


def amend_claim(
    session: Session,
    ticket: Ticket,
    claim: AssertedNotProvenClaim,
    dependent_document_answers: dict[str, str],
) -> Written:
    """Change a filed claim, keeping the row it supersedes."""
    _resolution_checked(session, ticket, claim)
    slug = claim_slug(claim.claim)
    stored = _must_exist(session, ticket, slug)
    payload = to_payload(claim, ClaimLifecycle(amends=stored.artifact_id))
    if _unchanged(stored, payload):
        # An identical rewrite writes nothing, so it obliges nothing: demanding
        # document answers for a no-op would make R10.1's idempotency
        # unreachable in practice.
        return Written(artifact_id=stored.artifact_id, amended=False)
    _obligations_met(session, stored, dependent_document_answers)
    return Written(artifact_id=_write(session, ticket, slug, payload).id, amended=True)


def record_measurement(
    session: Session,
    ticket: Ticket,
    slug: str,
    *,
    value: float,
    unit: str,
) -> Written:
    """Attach a measurement to a CODE_SITE claim's ceiling.

    The unit must be the ceiling's unit: 612 milliseconds does not exceed 500
    artifacts, and comparing a quantity to a quantity in another unit is how a
    ceiling gets closed for free. Non-finite values are refused for the same
    reason — `inf > 500` closes every ceiling and `nan > 500` closes none.
    """
    stored = _must_exist(session, ticket, slug)
    if stored.claim.member_kind is not AssertedNotProvenKind.CODE_SITE:
        raise ValueError(
            f"{slug!r} is a {stored.claim.member_kind.value} member and has no ceiling to "
            f"measure against; only a {AssertedNotProvenKind.CODE_SITE.value} member does"
        )
    if not math.isfinite(value):
        raise ValueError(
            f"measurement {value!r} is not a finite number, so it cannot be compared with the "
            f"ceiling of {stored.claim.ceiling_quantity} {stored.claim.ceiling_unit}"
        )
    if unit != stored.claim.ceiling_unit:
        raise ValueError(
            f"a measurement in {unit!r} cannot be compared with a ceiling in "
            f"{stored.claim.ceiling_unit!r}: measure the ceiling's own quantity"
        )
    payload = to_payload(
        stored.claim,
        ClaimLifecycle(
            amends=stored.artifact_id,
            outcome=stored.lifecycle.outcome,
            evidence=stored.lifecycle.evidence,
            measured_value=value,
            measured_unit=unit,
        ),
    )
    if _unchanged(stored, payload):
        return Written(artifact_id=stored.artifact_id, amended=False)
    return Written(artifact_id=_write(session, ticket, slug, payload).id, amended=True)


def close_claim(
    session: Session,
    ticket: Ticket,
    slug: str,
    *,
    outcome: ClaimOutcome,
    evidence: str,
    dependent_document_answers: dict[str, str],
    evidence_exists: bool,
) -> Written:
    """Settle a claim with an outcome.

    ``evidence_exists`` is the caller's answer to "does that pointer resolve in
    this checkout?" — the same shape/resolution seam as `routed_ticket`: this
    function decides what the outcome requires, the caller reads the filesystem.
    """
    stored = _must_exist(session, ticket, slug)
    _closable_as(stored, outcome, evidence, evidence_exists)
    payload = to_payload(
        stored.claim,
        ClaimLifecycle(
            amends=stored.artifact_id,
            outcome=outcome,
            evidence=evidence,
            measured_value=stored.lifecycle.measured_value,
            measured_unit=stored.lifecycle.measured_unit,
        ),
    )
    if _unchanged(stored, payload):
        return Written(artifact_id=stored.artifact_id, amended=False)
    _obligations_met(session, stored, dependent_document_answers)
    return Written(artifact_id=_write(session, ticket, slug, payload).id, amended=True)


def _closable_as(
    stored: StoredClaim,
    outcome: ClaimOutcome,
    evidence: str,
    evidence_exists: bool,
) -> None:
    if outcome is ClaimOutcome.PROVEN and not (evidence and evidence_exists):
        raise ValueError(
            f"closing {stored.slug!r} as {outcome.value} needs evidence that exists; "
            f"{evidence or '<nothing>'} does not resolve in this checkout"
        )
    if outcome is ClaimOutcome.CEILING_REACHED and not exceeds_ceiling(stored):
        measured = stored.lifecycle.measured_value
        raise ValueError(
            f"closing {stored.slug!r} as {outcome.value} needs a recorded measurement "
            f"exceeding its ceiling of {stored.claim.ceiling_quantity} "
            f"{stored.claim.ceiling_unit}; recorded: "
            f"{'<none>' if measured is None else measured}"
        )
    # FALSIFIED records that the named fallback now applies, and builds nothing.


def exceeds_ceiling(stored: StoredClaim) -> bool:
    """True when a recorded measurement exceeds this member's ceiling.

    Strictly exceeds, and only on a finite value in the ceiling's own unit:
    equal-to, zero, negative and non-finite are the four values that close a
    ceiling for free under the three most likely mis-codings.
    """
    measured = stored.lifecycle.measured_value
    ceiling = stored.claim.ceiling_quantity
    if measured is None or ceiling is None or not math.isfinite(measured):
        return False
    if stored.lifecycle.measured_unit != stored.claim.ceiling_unit:
        return False
    return measured > ceiling


def _must_exist(session: Session, ticket: Ticket, slug: str) -> StoredClaim:
    stored = latest_claim(session, ticket, slug)
    if stored is None:
        raise ValueError(
            f"{ticket.external_id} carries no claim {slug!r}; amend, measure and close all "
            "name an existing claim, and a typo must not quietly create a second member"
        )
    return stored


def _unchanged(stored: StoredClaim, payload: dict) -> bool:
    """True when ``payload`` says exactly what the latest row already says.

    Compared against the LATEST row, never against any stored revision: an
    implementation that matched any revision would treat a re-sent original as
    idempotent, and the operator's correction would be lost on the next retry
    of the earlier call.
    """
    current = to_payload(stored.claim, stored.lifecycle)
    return {key: value for key, value in payload.items() if key != "amends"} == {
        key: value for key, value in current.items() if key != "amends"
    }
