"""The spec a run started from, and whether it changed underneath the run.

A ticket's description, acceptance criteria and documents can change while a
stage runs or between rework rounds: a scope round, a split, a delegation, an
operator's edit. The run that started from the old text reports against it, and
nothing said the ground had moved.

So each run records a snapshot of what it was given at dispatch
(`AgentRun.spec_snapshot_json`, and its sha256 in `AgentRun.spec_hash`), and its
completion compares that snapshot with the ticket as it is then:

- any change is put on the ticket's history (`SPEC_CHANGED_UNDER_RUN`), naming
  what changed;
- a claimed pass from a run whose criteria gained or reworded one is held and
  the stage re-run (`stage_verdict`): a pass on the old criteria says nothing
  about the new ones. Settlements are already keyed by a criterion's exact
  text (`criterion_ledger`), so a reworded criterion is unsettled either way.

The run's own edits are not a change underneath it. A spec-writing tool call
made by the run moves its baseline forward (`rebaseline_after_own_edit`),
unless something else had already moved the spec since the baseline was taken.

Prior art: camp stores the sha256 of the plan document a run started from and
sets `DocHashChanged` when it re-hashes differently on read (recorded on
lg-milestone-that-2489's "Prior art — reference survey" document).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from loregarden.models.domain import AgentRun, RunStatus, Ticket
from loregarden.models.domain.orchestrator_decisions import OrchestratorDecision
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.ticket_documents import document_index
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session


class SpecSnapshot(BaseModel):
    """What a run was given about its ticket, as recorded at dispatch.

    The description is kept as its hash: only whether it changed is reported.
    Criteria are kept whole, so a change can name the criterion. Documents are
    the ones `get_ticket` lists (the ticket's and its ancestors'), by artifact
    id: a new version of a document is a new artifact, so a changed id is a
    changed document. The title is kept to name it.
    """

    model_config = ConfigDict(frozen=True)

    description_sha: str
    criteria: tuple[str, ...]
    documents: dict[str, str]

    def digest(self) -> str:
        canonical = self.model_dump_json(include={"description_sha", "criteria"}) + "".join(
            f"\n{artifact_id}" for artifact_id in sorted(self.documents)
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class SpecDrift:
    """How the spec changed between a run's dispatch and now."""

    description_changed: bool
    criteria_added: tuple[str, ...]
    criteria_removed: tuple[str, ...]
    documents_added: tuple[str, ...]
    documents_removed: tuple[str, ...]
    documents_changed: tuple[str, ...]

    @property
    def unsettles_criteria(self) -> bool:
        """Whether the ticket now carries a criterion the run was not given."""
        return bool(self.criteria_added)

    def summary(self) -> str:
        parts: list[str] = []
        if self.description_changed:
            parts.append("the description changed")
        if self.criteria_added:
            parts.append(f"{len(self.criteria_added)} criterion(s) added or reworded")
        if self.criteria_removed:
            parts.append(f"{len(self.criteria_removed)} criterion(s) removed or reworded")
        for verb, titles in (
            ("added", self.documents_added),
            ("removed", self.documents_removed),
            ("rewritten", self.documents_changed),
        ):
            if titles:
                parts.append(f"document(s) {verb}: {', '.join(repr(t) for t in titles)}")
        return "; ".join(parts)


def spec_snapshot(session: Session, ticket: Ticket) -> SpecSnapshot:
    """The ticket's spec as a run dispatched now would be given it."""
    return SpecSnapshot(
        description_sha=hashlib.sha256((ticket.description or "").encode()).hexdigest(),
        criteria=tuple(load_criteria(ticket.acceptance_criteria_json)),
        documents={item["artifact_id"]: item["title"] for item in document_index(session, ticket)},
    )


def _store(session: Session, run: AgentRun, snapshot: SpecSnapshot) -> None:
    run.spec_snapshot_json = snapshot.model_dump_json()
    run.spec_hash = snapshot.digest()
    session.add(run)
    session.commit()


def stamp_run_spec(session: Session, run: AgentRun, ticket: Ticket) -> None:
    """Record the spec `run` is given, at the moment its prompt is built. Commits."""
    _store(session, run, spec_snapshot(session, ticket))


def recorded_spec(run: AgentRun) -> SpecSnapshot | None:
    """The snapshot stored on `run`, or None when it has none.

    None for runs dispatched before the column existed and for a path that
    never built a stage prompt: no record, not "unchanged".
    """
    if not run.spec_snapshot_json:
        return None
    return SpecSnapshot.model_validate_json(run.spec_snapshot_json)


def spec_drift(before: SpecSnapshot, after: SpecSnapshot) -> SpecDrift | None:
    """What changed from `before` to `after`, or None when nothing did."""
    if before.digest() == after.digest():
        return None
    old_ids, new_ids = set(before.documents), set(after.documents)
    added = {after.documents[i] for i in new_ids - old_ids}
    removed = {before.documents[i] for i in old_ids - new_ids}
    rewritten = added & removed
    return SpecDrift(
        description_changed=before.description_sha != after.description_sha,
        criteria_added=tuple(c for c in after.criteria if c not in before.criteria),
        criteria_removed=tuple(c for c in before.criteria if c not in after.criteria),
        documents_added=tuple(sorted(added - rewritten)),
        documents_removed=tuple(sorted(removed - rewritten)),
        documents_changed=tuple(sorted(rewritten)),
    )


def run_spec_drift(session: Session, ticket: Ticket, run: AgentRun) -> SpecDrift | None:
    """How `ticket`'s spec changed since `run` was dispatched, or None."""
    before = recorded_spec(run)
    if before is None:
        return None
    return spec_drift(before, spec_snapshot(session, ticket))


def record_spec_drift(session: Session, ticket: Ticket, run: AgentRun) -> SpecDrift | None:
    """Put a run's completion on the history when its spec changed under it."""
    drift = run_spec_drift(session, ticket, run)
    if drift is None:
        return None
    unsettled = (
        " A pass does not settle the new criteria; the stage runs again on them."
        if drift.unsettles_criteria
        else ""
    )
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.SPEC_CHANGED_UNDER_RUN,
        stage_key=run.stage_key,
        reason=f"Run {run.run_code} finished on a spec that changed after it started: "
        f"{drift.summary()}.{unsettled}",
        run_id=run.id,
        evidence={
            "dispatched_spec_hash": run.spec_hash,
            "current_spec_hash": spec_snapshot(session, ticket).digest(),
            "description_changed": drift.description_changed,
            "criteria_added": list(drift.criteria_added),
            "criteria_removed": list(drift.criteria_removed),
            "documents_added": list(drift.documents_added),
            "documents_removed": list(drift.documents_removed),
            "documents_changed": list(drift.documents_changed),
        },
    )
    return drift


def unsettled_criteria_detail(drift: SpecDrift) -> str:
    """What a run sent back for a criterion it was not given is told to do."""
    listed = "\n".join(f"- {criterion}" for criterion in drift.criteria_added)
    return (
        "The ticket's acceptance criteria changed after this run started, so its pass "
        "does not settle these, which it was not given. Check the work against them:\n"
        f"{listed}"
    )


def own_edit_baseline(
    session: Session, run_id: str
) -> tuple[AgentRun, Ticket, SpecSnapshot] | None:
    """Before a spec-writing tool call: the running run, its ticket and its spec now.

    None when the call is not from a running run with a recorded spec, so there
    is no baseline to move.
    """
    run = session.get(AgentRun, run_id) if run_id else None
    if run is None or run.status is not RunStatus.RUNNING or not run.ticket_id:
        return None
    if recorded_spec(run) is None:
        return None
    ticket = session.get(Ticket, run.ticket_id)
    if ticket is None:
        return None
    return run, ticket, spec_snapshot(session, ticket)


def rebaseline_after_own_edit(
    session: Session, run: AgentRun, ticket: Ticket, before: SpecSnapshot
) -> None:
    """After the run's own spec-writing call: move its baseline over what it wrote.

    Only when the spec just before the call was still the run's baseline. If
    something else had already changed it, the baseline stays, so that change
    is still reported (the run's own edit is then reported with it).
    """
    session.refresh(run)
    recorded = recorded_spec(run)
    if recorded is None or recorded.digest() != before.digest():
        return
    session.refresh(ticket)
    after = spec_snapshot(session, ticket)
    if after.digest() != before.digest():
        _store(session, run, after)
