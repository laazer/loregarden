"""Who wrote a land-verification receipt, as a state invariant.

The receipt (`ArtifactKind.LAND_VERIFICATION`) is read as PROOF by three
consumers that judge the agent whose landing it describes —
`land_verification_coverage.covered_landings`, `landing_status._refusals` and
`landing_refused.recent_refusals` — and none of them checks who wrote the row.
`artifact_kind_owners.refuse_if_writer_owned` at the artifact doors is the
prevention (lg-milestone-that-1423); this is the backstop that reports a row
that got in anyway.

Its own module rather than another hundred lines of `invariant_registry`, which
is at its size cap: the registry assembles `INVARIANTS` and the detectors live
beside the service whose state they read, the way `landing_carried_work` and
`scope_invariants` already do.
"""

from __future__ import annotations

import json
from datetime import date

from loregarden.models.domain import Artifact, ArtifactKind, InvariantReentry, Ticket
from loregarden.services.invariants import Invariant, Violation
from sqlalchemy import case, func, or_, select
from sqlmodel import Session, col

#: When the writer-owned-kind guard landed, and so the line between backlog and
#: re-entry for `LAND_VERIFICATION_RECEIPT_IS_SERVICE_WRITTEN`. The day AFTER
#: the change, for the reason `LAND_VERIFICATION_SINCE` records: a receipt
#: written earlier on the same day predates the guard by hours, and
#: `Invariant.since` is read as that day's midnight.
#:
#: Measured read-only on the live database 2026-10-23, 32,115 artifacts: 413
#: `land_verification` rows, every one with `run_id IS NULL` and every one
#: carrying exactly the nine keys `record_land_verification` writes — zero
#: forgeries by either discriminator, so there is no backlog to repair.
LAND_VERIFICATION_RECEIPT_WRITER_SINCE = date(2026, 11, 13)

#: The receipt keys `record_land_verification` writes that a forger has no
#: reason to copy. `status` and `stage_key` are excluded on purpose: they are
#: the only two `_has_a_passing_receipt` and the client's `reportVerdict` read,
#: so they are exactly what a forgery DOES carry, and a detector keyed on them
#: would see nothing.
_SERVICE_WRITTEN_RECEIPT_KEYS = frozenset(
    {"outcome", "branch", "target", "candidate_sha", "command", "exit_code", "output"}
)


def _receipt_body_keys(body: str | None) -> frozenset[str]:
    """The keys of a receipt body, or none at all when it cannot be read.

    A body that is not readable JSON, or is JSON that is not an object, carries
    no key this detector requires — which makes it a violation rather than
    taking the pass down with it. `run_invariant` is one step of
    `PERIODIC_STEPS`, and a detector that raises reports nothing for every
    ticket from then on while the surface says there are no violations.
    """
    try:
        return frozenset(json.loads(body or "").keys())
    except (ValueError, AttributeError):
        return frozenset()


def _body_is_missing_a_required_key():
    """`True` for a receipt body that does not carry every key the service writes.

    Key PRESENCE, not value: `json_type(body, '$.key')` is `NULL` only when the
    path is absent, and returns the string `'null'` for a key whose value is
    JSON null — which `json_extract` cannot tell apart, and which the Python
    `.keys()` test this replaces counted as present.

    The whole test sits inside a `CASE` guarded by `json_valid` and
    `json_type(body) = 'object'`, and that guard is load-bearing rather than
    defensive: SQLite's `json_type`/`json_extract` RAISE `malformed JSON` on an
    unreadable body (verified on 3.51.0), `OR` is not a guaranteed short
    circuit, and `CASE` is the one construct documented to evaluate only the
    branch it took. Unguarded, one unreadable row raises inside
    `run_invariant` — one step of `PERIODIC_STEPS` — and every later pass
    reports nothing while the surface says there are no violations. A body that
    cannot be read carries none of the required keys, so the `ELSE` branch says
    so instead.
    """
    readable = func.json_valid(col(Artifact.content_json)) & (
        func.json_type(col(Artifact.content_json)) == "object"
    )
    absent = or_(
        *[
            func.json_type(col(Artifact.content_json), f"$.{key}").is_(None)
            for key in sorted(_SERVICE_WRITTEN_RECEIPT_KEYS)
        ]
    )
    return case((readable, absent), else_=True)


def _receipts_no_service_wrote(session: Session) -> list[Violation]:
    """Land-verification receipts the owning service cannot have written.

    Two discriminators, both after-the-fact properties of the row, and both
    asked in SQL so that the rows this materialises are the rows it reports:

    - **a `run_id`.** `record_land_verification` sets none, so a stamped row
      came through a tool that attributes the agent's run — which is every door
      the guard covers.
    - **a missing key.** The service writes a fixed nine-key body; a forgery
      only needs the two `_has_a_passing_receipt` reads.

    Which is also the limit, and it is stated rather than implied: a forger that
    passes `run_id=None` AND copies all nine keys is indistinguishable from the
    service's own output after the fact. This invariant is a BACKSTOP for the
    forgery shapes that are visible; `artifact_kind_owners.refuse_if_writer_owned`
    at the doors is the prevention.

    **Not bounded by `created_at >= LAND_VERIFICATION_RECEIPT_WRITER_SINCE`**,
    and that is a decision rather than an omission. A report-only invariant
    re-reports its pre-`since` backlog on every pass BY DESIGN, because nothing
    else will ever clear it, so a `created_at` bound would silence an old forged
    receipt found next month; today's measured zero is a fact about today.
    `since` does its work where it already is — telling backlog from re-entry
    once a violation is reported. What bounds the load instead is the
    discriminator itself, in the `where`.

    Named columns rather than whole `Artifact` entities, in the shape
    `invariant_registry._landed_without_a_verified_suite` already uses for this
    same kind and column: one violation is one ROW, and the only body this reads
    into Python belongs to a row it is about to report.
    """
    # `sqlalchemy.select` and `Session.execute`, not sqlmodel's pair: the five
    # named columns below are one past the four sqlmodel's `select` overloads
    # type, and dropping a column to fit the typing would put a body read or a
    # timestamp back on the whole-entity path this criterion exists to remove.
    candidates = session.execute(
        select(
            col(Artifact.id),
            col(Artifact.ticket_id),
            col(Artifact.run_id),
            col(Artifact.created_at),
            col(Artifact.content_json),
        )
        .where(
            col(Artifact.kind) == ArtifactKind.LAND_VERIFICATION,
            or_(col(Artifact.run_id).is_not(None), _body_is_missing_a_required_key()),
        )
        .order_by(col(Artifact.created_at), col(Artifact.id))
    ).all()
    found: list[Violation] = []
    for receipt_id, ticket_id, run_id, created_at, body in candidates:
        missing = sorted(_SERVICE_WRITTEN_RECEIPT_KEYS - _receipt_body_keys(body))
        ticket = session.get(Ticket, ticket_id)
        named = ticket.external_id if ticket else ticket_id
        reason = (
            f"carries run_id {run_id}" if run_id else f"its body is missing {', '.join(missing)}"
        )
        found.append(
            Violation(
                subject_id=receipt_id,
                ticket_id=ticket_id,
                entered_at=created_at,
                detail=(
                    f"{named}: land-verification receipt {receipt_id} {reason}, so "
                    "record_land_verification did not write it"
                ),
            )
        )
    return found


LAND_VERIFICATION_RECEIPT_IS_SERVICE_WRITTEN = Invariant(
    name="land_verification_receipt_is_service_written",
    ticket="lg-milestone-that-1423",
    statement="every land-verification receipt was written by record_land_verification",
    detect=_receipts_no_service_wrote,
    # Report-only: the row is garbage, but deleting a ticket's artifact
    # unprompted is a person's call — and a receipt this detector reports may be
    # the only record of a landing nobody has looked at yet.
    repair=None,
    reentry=InvariantReentry.DEFECT,
    since=LAND_VERIFICATION_RECEIPT_WRITER_SINCE,
    prevented_by=(
        "server/tests/test_artifact_kind_owners.py"
        "::test_the_census_refuses_every_writer_owned_kind_at_every_agent_facing_door"
    ),
    unrepaired_action=(
        "Read the receipt the finding names on the ticket's Outputs tab. If no verifier run "
        "produced it, delete the row and re-read the landing: `land_verification_ran` will "
        "report it again, which is the question it was silencing. If a verifier did produce "
        "it, the writer changed shape and this detector's required keys are what to fix."
    ),
)
