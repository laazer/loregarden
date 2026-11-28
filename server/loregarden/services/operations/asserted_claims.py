"""The asserted-not-proven debt class as two operations: write one, read the class.

`loregarden_record_asserted_not_proven` and `loregarden_open_claims` were
written as table-dispatched MCP tools with hand-written schemas, the shape
`mcp/fix_coverage_tools.py` still has. The operations seam landed after them,
and a tool declared outside it carries a second copy of its own arguments —
the drift `tests/test_operations.py` exists to refuse. Both now declare their
arguments once, as the models below, and MCP and the CLI reach them through
`operation_call`.

Neither is offered on REST: the class has NO GRAPHICAL SURFACE (AC12), and the
read tool's response is its only operator-readable surface. A later stage
proposing a page comes back through ui-design first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loregarden.models.domain import (
    AssertedClaimStatus,
    AssertedNotProvenKind,
    ClaimOperation,
    ClaimOutcome,
    Ticket,
    Workspace,
)
from loregarden.services.asserted_not_proven.listing import open_claims, owed_test_resolves
from loregarden.services.asserted_not_proven.record import (
    amend_claim,
    claim_slug,
    close_claim,
    record_measurement,
    register_claim,
)
from loregarden.services.asserted_not_proven.schemas import (
    AssertedNotProvenClaim,
    parse_claim,
)
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True, str_strip_whitespace=True)

#: The claim's own fields, taken from the model so a field added there cannot
#: be silently unreachable through the tool.
_CLAIM_FIELDS = frozenset(AssertedNotProvenClaim.model_fields)

#: Why neither operation is offered on the page.
_NO_PAGE = (
    "the class has no graphical surface (AC12); loregarden_open_claims' response is "
    "the only operator-readable surface it has"
)


class RecordAssertedNotProvenCall(BaseModel):
    """The write tool's flat arguments: the claim's own fields, plus what an
    amend, a measurement and a close each need. `AssertedNotProvenClaim`
    validates the claim itself, and words its own refusals."""

    model_config = _LENIENT

    ticket_id: str = Field(min_length=1)
    """Ticket UUID or external id."""
    operation: ClaimOperation
    """record (file it), amend (change it), measure (attach a measurement to a code
    site's ceiling) or close (settle it with an outcome)."""
    claim: str = ""
    """The statement believed without proof. Its identity."""
    member_kind: AssertedNotProvenKind | None = None
    """behaviour (observed, untested), code_site (knowingly insufficient, marked with
    `ceiling:`), drafted_default (a drafted number, frozen and challenged) or
    document_defect (a defect in a document this depends on)."""
    qualifies_because: str = ""
    """Why this one joins the class."""
    owed_artifact: str = ""
    """The resolvable pointer it is owed. Never prose — prose goes in owed_description."""
    owed_description: str = ""
    """A measurement or reproduction that is not itself a file. Never a substitute for
    owed_artifact."""
    owed_environment: str = ""
    """The environment the proof needs, if it cannot be proven where we develop."""
    portable_subset: str = ""
    """What was split out of it and landed now. Required with owed_environment."""
    proven_today: str = ""
    """The half of the guarantee proven today. Required with owed_environment."""
    fallback: str = ""
    """Where this goes if the claim turns out false — named, and saying it is 'not built'."""
    upstream_id: str = ""
    """The identifier the claim keeps from where it was decided."""
    citation: str = ""
    """The source locator it is traceable to."""
    dependent_documents: list[str] = []
    """Documents a change to this claim obliges an edit to. Each must be answered in
    dependent_document_answers before an amend or a close."""
    ceiling: str = ""
    """code_site: the quantity at which this stops holding."""
    ceiling_quantity: float | None = None
    """code_site: that quantity as a number."""
    ceiling_unit: str = ""
    """code_site: the unit of that quantity. A measurement in another unit cannot close it."""
    upgrade_path: str = ""
    """code_site: the path off the shortcut."""
    frozen: bool | None = None
    """drafted_default: the number is frozen against change."""
    challenger: str = ""
    """drafted_default: the artifact class or gate that owns challenging it, in backticks.
    A role is refused."""
    routed_ticket: str = ""
    """document_defect: the external id of the ticket the defect was routed to."""
    dependent_document_answers: list[str] = []
    """amend/close: one '<document> :: <answer>' per dependent document, the answer being
    the edit that was made or a ticket external id that resolves."""
    value: float | None = None
    """measure: the measured value."""
    unit: str = ""
    """measure: its unit, which must be the ceiling's unit."""
    outcome: ClaimOutcome | None = None
    """close: proven (the owed artifact landed), falsified (the named fallback now applies,
    and is still not built) or ceiling_reached."""
    evidence: str = ""
    """close/proven: the checked-in test that proves it, which must exist."""

    def claim_fields(self) -> dict[str, Any]:
        """Only what the caller actually sent, as the claim model reads it.

        `exclude_unset` rather than every default: a field the caller never
        named must not arrive at the claim model as an answer, since the model
        refuses a field carried by another kind.
        """
        sent = self.model_dump(mode="json", exclude_unset=True)
        return {
            name: sent[name] for name in sent if name in _CLAIM_FIELDS and sent[name] is not None
        }


class OpenClaimsCall(BaseModel):
    """The read tool's four filters, and no others. A filter nobody decided is
    a filter nobody can explain (AC12(e))."""

    model_config = _LENIENT

    workspace_slug: str = Field(min_length=1)
    """Workspace the class is scoped to."""
    ticket_id: str = ""
    """Narrow to one ticket (UUID or external id)."""
    member_kind: AssertedNotProvenKind | None = None
    """Narrow to one kind."""
    status: AssertedClaimStatus = AssertedClaimStatus.OPEN
    """open (the default; includes closable), closable, settled, or all."""


def repo_root(workspace: Workspace | None) -> Path | None:
    """The checkout `closable` and the code-site scan are computed against.

    Through `resolve_workspace_root`, never `Path(workspace.repo_path)`. The
    live `loregarden` row stores `repo_path = "."` — every other workspace is
    absolute — and a relative Path resolves against whatever cwd the caller
    happens to have. Measured from the CLI (cwd `server/`) before this was
    fixed: the scan read 1,691 files of a 3,489-file tree and reported
    `scan_truncated: false`, the exact "a partial scan must not read as the
    complete class" failure R7 exists to prevent; the scanned paths came out
    relative to `server/`, so a code-site member's `path/to/file.py:<line>` did
    not resolve from the repo root; markers under `.lefthook/scripts/` and
    `client/` were never read at all; and no member could ever be `closable`,
    so the enumerate tool's primary ordering never fired.

    None when the workspace names no repository: the answer is then about the
    stored rows alone, and `scanned_files` says zero rather than pretending.
    """
    if workspace is None or not workspace.repo_path:
        return None
    return resolve_workspace_root(workspace)


def _answers(raw: list[str]) -> dict[str, str]:
    """`['<document> :: <answer>', ...]` as a mapping.

    A list of pairs rather than an object, because the MCP schemas here are
    flat; an entry with no separator is refused rather than dropped, since a
    dropped answer reads as an unanswered document and sends the author
    chasing an edit they already made.
    """
    answers: dict[str, str] = {}
    for entry in raw:
        document, separator, answer = entry.partition("::")
        if not separator:
            raise ValueError(
                f"dependent_document_answers entry {entry!r} has no '::' separator; write it "
                "as '<document> :: <the edit that was made, or a ticket external id>'"
            )
        answers[document.strip()] = answer.strip()
    return answers


def _claim_from(call: RecordAssertedNotProvenCall, ticket: Ticket) -> AssertedNotProvenClaim:
    try:
        return parse_claim(call.claim_fields())
    except ValueError as exc:
        raise ValueError(
            f"Asserted-not-proven claim for {ticket.external_id} not recorded: {exc}"
        ) from exc


def _dispatch(
    session: Session,
    ticket: Ticket,
    workspace: Workspace | None,
    call: RecordAssertedNotProvenCall,
):
    if call.operation is ClaimOperation.RECORD:
        return register_claim(session, ticket, _claim_from(call, ticket))
    if call.operation is ClaimOperation.AMEND:
        return amend_claim(
            session,
            ticket,
            _claim_from(call, ticket),
            _answers(call.dependent_document_answers),
        )
    slug = claim_slug(call.claim)
    if call.operation is ClaimOperation.MEASURE:
        if call.value is None:
            raise ValueError("measuring a ceiling needs a value")
        return record_measurement(session, ticket, slug, value=call.value, unit=call.unit)
    if call.outcome is None:
        raise ValueError("closing a claim needs an outcome: proven, falsified or ceiling_reached")
    return close_claim(
        session,
        ticket,
        slug,
        outcome=call.outcome,
        evidence=call.evidence,
        dependent_document_answers=_answers(call.dependent_document_answers),
        evidence_exists=owed_test_resolves(repo_root(workspace), call.evidence),
    )


def _record(session: Session, call: RecordAssertedNotProvenCall, _caller: Caller) -> dict[str, Any]:
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=call.ticket_id)
    workspace = workspace_for_ticket(session, ticket)
    written = _dispatch(session, ticket, workspace, call)
    return {
        "ok": True,
        "artifact_id": written.artifact_id,
        "amended": written.amended,
        "ticket": ticket.external_id,
        "operation": call.operation.value,
    }


def _open(session: Session, call: OpenClaimsCall, _caller: Caller) -> dict[str, Any]:
    workspace = session.exec(select(Workspace).where(Workspace.slug == call.workspace_slug)).first()
    ticket_id = call.ticket_id
    if ticket_id:
        ticket_id = OrchestrationCallbackService(session).resolve_ticket(ticket_id=ticket_id).id
    return open_claims(
        session,
        workspace_slug=call.workspace_slug,
        repo_root=repo_root(workspace),
        ticket_id=ticket_id or None,
        member_kind=call.member_kind,
        status=call.status,
    )


ASSERTED_CLAIMS = (
    Operation(
        id=OperationId.ASSERTED_CLAIM_RECORD,
        effect=Effect.WRITE,
        args=RecordAssertedNotProvenCall,
        handler=_record,
        unavailable_on={Transport.REST: _NO_PAGE},
    ),
    Operation(
        id=OperationId.ASSERTED_CLAIMS_OPEN,
        effect=Effect.READ,
        args=OpenClaimsCall,
        handler=_open,
        unavailable_on={Transport.REST: _NO_PAGE},
    ),
)
