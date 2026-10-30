"""Closing out a run a handoff opened and could not hand over.

Both handoff surfaces — the terminal-handoff route and the external-harness
checkout — stamp a run RUNNING and commit it *before* they try to prepare the
tree. When that preparation fails there is a run with no agent behind it and a
stage that still reads RUNNING, for ``fail_interrupted_runs`` to kill on
somebody else's pass. One settle, imported by both, because two copies that
agree today are the state this exists to prevent (lg-milestone-that-974 R7).

It lives here rather than in either caller: the terminal route is not an
external harness, so owning the shared settle from ``external_harness`` would
name the wrong owner, and that module is already a hotspot.

It takes no ``surface``/``kind`` string. Which surface failed is already in each
caller's own logging, and a closed set of values is an enum here, not a string.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import (
    AgentRun,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.run_service import RunService
from loregarden.services.stage_retry_budget import refund_stage_dispatch_charged_before
from sqlmodel import Session

logger = logging.getLogger(__name__)


def settle_failed_handoff_run(
    session: Session,
    run_svc: RunService,
    run: AgentRun,
    ticket: Ticket,
    *,
    detail: str,
) -> None:
    """Close out one run the handoff opened, and say nothing about the counter.

    The per-run half of `settle_failed_handoff`, and the primitive the fan-out
    calls once per stamped member. A single-run caller wants the composition
    below, not this: it is the composition that hands the dispatch charge back,
    and a run settled without that refund spends an attempt on work that never
    started.

    Three steps, because the orphan shape in ``run_service`` settles the *run*
    and hands back its charge but says nothing about the stage: the stage is put
    back to PENDING, which is what it was before a dispatch that never attempted
    the work. Not FAILED or BLOCKED — those are states an operator has to clear
    by hand, which is the opposite of handing the dispatch charge back — and the
    workflow does not advance, or the next checkout would serve the stage
    *after* one whose work never started.

    Only ever called for a run **this** handoff stamped RUNNING. A resumed run
    is work the harness is already holding; reaping it is what
    ``_resumable_stage_run`` exists to prevent.

    The fourth step is the decision, and it is recorded **here** rather than in
    the composition below even though the refund is not. That asymmetry is
    deliberate and load-bearing: ``external_harness._settle_fanned_out_members``
    calls this once per stamped member and refunds once for the whole pass
    (lg-milestone-that-1213), so N members leave N decisions against one marker.
    A decision is a statement about one run; the refund is a statement about one
    pass. Move this call into the composition to make them agree and the surface
    that stamps the most runs goes silent, with every test green but one count
    (976 R1).
    """
    logger.warning("Handoff for %s failed: %s", ticket.external_id, detail)
    run_svc.orchestration.complete_run(
        run, status=RunStatus.FAILED, stderr=detail, advance_workflow=False
    )
    if run.stage_key:
        run_svc.orchestration.finalize_stage(ticket, run.stage_key, status=StageStatus.PENDING)
    # After the reset and before the refresh, because `event_bus.publish` commits
    # inline (`core/event_bus.py:90-91`): a decision recorded first is on the rail
    # saying the stage was put back to PENDING whether or not the write that puts
    # it there then succeeds, and that sentence is the one fact the operator acts
    # on. A reset that raises must leave no line rather than a wrong one.
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.SETTLED_FAILED_HANDOFF,
        stage_key=run.stage_key,
        reason=_settle_reason(run, detail),
        run_id=run.id,
        evidence={
            "run_code": run.run_code,
            "stage_reset": bool(run.stage_key),
            # The whole of it, so the rail's `<details>` can show git's output
            # without the operator leaving for the run's stderr. The sentence
            # above carries only its first line.
            "detail": detail,
        },
    )
    session.refresh(ticket)


def _settle_reason(run: AgentRun, detail: str) -> str:
    """The one line the history rail renders for a settled handoff run.

    Here rather than in `orchestrator_decisions`, which is deliberately
    decision-agnostic and formats nothing: this is the wording of one kind, and
    the single publisher stays a publisher.

    Two arms, because only one of them reset a stage. A run that names no stage
    had none put back, so that arm must not claim PENDING or "again" — a line
    asserting a transition that did not happen is worse than no line, for the
    same reason the cause clause below is dropped rather than left dangling.

    The cause is `detail` up to its first newline, by guarding rather than
    indexing: a caller whose failure text is empty (a `ValueError("")`, a
    command that failed silently) gets a sentence one clause shorter, not a
    raise and not a connector with nothing after it.
    """
    cause = (detail or "").split("\n", 1)[0].strip()
    opened = (
        f"Stage {run.stage_key}'s run {run.run_code}" if run.stage_key else f"Run {run.run_code}"
    )
    sentence = f"{opened} never attempted the work"
    if cause:
        sentence = f"{sentence} — {cause}"
    if not run.stage_key:
        return f"{sentence}. No stage was behind it, so none was put back."
    return f"{sentence}. The stage was put back to PENDING, ready to be dispatched again."


def settle_failed_handoff(
    session: Session,
    run_svc: RunService,
    run: AgentRun,
    ticket: Ticket,
    *,
    detail: str,
) -> None:
    """Settle the run the handoff opened one call earlier, and refund its charge.

    The composition every single-run caller wants: one run, one dispatch charge,
    handed back by the time cutoff that is exact for a caller whose pass holds
    exactly one run (lg-milestone-that-974 AC12).

    It is deliberately **not** what the external fan-out calls per member. A
    parallel pass is charged one marker for the whole pass, so N of these would
    hand back N, destroying N-1 earlier passes' charges and resetting the stage
    retry breaker (lg-milestone-that-1213). That caller composes
    `settle_failed_handoff_run` per member with one
    `refund_stage_dispatch_charged_since` for the pass.
    """
    settle_failed_handoff_run(session, run_svc, run, ticket, detail=detail)
    if run.stage_key:
        refund_stage_dispatch_charged_before(session, ticket.id, run.stage_key, run.created_at)
