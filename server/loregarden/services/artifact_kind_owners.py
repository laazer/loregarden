"""Which `artifacts.kind` values a named writer owns, and the refusal that says so.

`artifacts.kind` is an open `str` column on purpose — ~27 agent-coined kinds in
the live database are that feature being used, and `ArtifactKind`'s own
docstring keeps the column open rather than closing a real capability to tidy
it. But one TIER of those kinds is read **as proof** by consumers that judge the
agent writing them, and nothing checked that the agent was not the writer.
`land_verification_coverage.covered_landings` clears a landing when any
`land_verification` artifact on the ticket says `status == "passed"`, checking
nothing about `run_id`, `commit_sha`, or whether `candidate_sha` relates to
`ticket.landed_sha` — so the agent whose landing the detector judges could mint
the row that silences it (lg-milestone-that-1423).

So: write authority is a property of the CALLER — did this string come from a
model? — and not of the function. This module is a pure mapping and a pure
refusal, called as the first statement of each agent-facing door that takes a
kind as a free-form string. The dedicated writers pass their kind as code from
inside the service that owns it and never reach a door at all, so they need no
exemption and cannot be exempted by reflex: `mcp/fix_coverage_tools.py`,
`mcp/ux_answers_tools.py` and `mcp/artifact_tools.attach_evidence` all pass a
reserved kind through `OrchestrationCallbackService.attach_artifact`, which is
exactly why the guard is not in that 14-caller method.

Owners are named as STRINGS. That keeps this module importing nothing but the
enums, so it carries none of the land-verification dependency tree and stays
cycle-free from `mcp/` and `api/` — and it lives in `services/` because `api/`
must not import `mcp/`.

The three `unresolved_*` kinds are here as deliberate defence in depth. The
service method keeps its own narrower `UnresolvedArtifactKind` refusal, whose
scope is WIDER than these doors because it covers every internal caller
(gate_recovery, parallel_stage, handoff_writer, the inbox). Two guards, two
scopes, on purpose — not a duplicate for a later refactor to tidy away.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from loregarden.models.domain.enums import ArtifactKind, UnresolvedArtifactKind

#: Writer-owned kind → the service or tool that owns producing it.
#:
#: A kind is reserved when **a consumer reads it as proof and a named writer
#: owns producing it** — not when an agent merely should not usually write one.
#: Deliberately NOT here, because reserving one breaks every stage that attaches
#: its own output: the open `ArtifactKind` members (`context`, `log`, `diff`,
#: `error`, `test`, `plan`, `pr`, `workflow_gate`, …) and the agent coinages the
#: column exists for. `handoff_not_validated` is not reserved either — it
#: records the ABSENCE of proof, so an agent writing one claims nothing.
#:
#: A `MappingProxyType` rather than a `dict`: every door reads this on every
#: call, and a plain dict lets any importer delete `land_verification` from the
#: authority list for the rest of the process.
WRITER_OWNED_KINDS: Mapping[str, str] = MappingProxyType(
    {
        ArtifactKind.LAND_VERIFICATION.value: (
            "loregarden.services.land_verification.record_land_verification"
        ),
        # The dedicated tool stamps `commit_sha` server-side, because an agent
        # choosing its own sha can claim proof against a commit its work
        # predates. The free-form door bypasses that stamp.
        ArtifactKind.EVIDENCE.value: "loregarden.services.evidence",
        ArtifactKind.FIX_COVERAGE.value: "loregarden.services.fix_coverage",
        ArtifactKind.UX_ANSWERS.value: "loregarden.services.ux_answers",
        ArtifactKind.HANDOFF.value: "loregarden.services.handoff_writer",
        UnresolvedArtifactKind.OPEN.value: "loregarden.services.unresolved_ledger",
        UnresolvedArtifactKind.WAIVER.value: "loregarden.services.unresolved_ledger",
        UnresolvedArtifactKind.CLEAR.value: "loregarden.services.unresolved_ledger",
    }
)

#: Where the agent goes instead. Every refusal names one, because R2 measured
#: the case where it matters: two `handoff` rows in the live database were
#: hand-written in 2026-09 after `write_handoff` refused them on a catalog gap.
#: Under this guard that agent can no longer file one, so the refusal has to say
#: what to do instead rather than only what it may not do.
_INSTEAD: Mapping[str, str] = MappingProxyType(
    {
        ArtifactKind.LAND_VERIFICATION.value: (
            "no tool — a landing's receipt is written by the verifier, never by an agent"
        ),
        ArtifactKind.EVIDENCE.value: "loregarden_attach_evidence",
        ArtifactKind.FIX_COVERAGE.value: "loregarden_record_fix_coverage",
        ArtifactKind.UX_ANSWERS.value: "loregarden_record_ux_answers",
        ArtifactKind.HANDOFF.value: "loregarden_write_handoff",
        UnresolvedArtifactKind.OPEN.value: (
            "no tool — the unresolved ledger is written by the stage report it reads"
        ),
        UnresolvedArtifactKind.WAIVER.value: (
            "no tool — a waiver is recorded from the stage report's `waivers`"
        ),
        UnresolvedArtifactKind.CLEAR.value: (
            "no tool — a clear is recorded when the producing stage resolves the name"
        ),
    }
)


def refuse_if_writer_owned(
    kind: str,  # py-org: allow-string - the open artifact kind column, decided here from its value
) -> None:
    """Refuse an agent-supplied `kind` that a named writer owns.

    Exact, and deliberately so. The writer is what owes this function the value
    the row will carry — `ticket_documents.write_document` normalizes its kind
    and must do so BEFORE it asks — because a `.strip().lower()` in here would
    reserve `EVIDENCE` and ` evidence`, neither of which any reader matches on,
    and would start refusing coinages the open column exists for. The reader
    side already argues it: `_has_a_passing_receipt`'s comparison is exact
    because normalizing there "would clear a landing whose receipt the client
    itself colours `warn`".

    Pure: no Session, no database, no state. The answer depends on nothing but
    the string, which is what lets it sit at a boundary rather than at a writer.
    """
    # A kind an agent sent as a JSON list or object is unhashable, and a mapping
    # lookup on one is a `TypeError` that reaches the agent as a crash it cannot
    # read and an operator as a 500. It is not a reserved kind; the writer's own
    # column constraint is what rejects it.
    if kind.__hash__ is None:
        return
    owner = WRITER_OWNED_KINDS.get(kind)
    if owner is None:
        return
    raise ValueError(
        f"kind {kind!r} is owned by {owner} and cannot be attached by an agent: a record a "
        f"detector reads as proof must not be writable by the subject it judges. "
        f"Use {_INSTEAD[kind]}."
    )
