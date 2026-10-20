"""The one way to leave a ticket blocked: classify it, then offer the repair turn.

749 gave a block a kind and 750 gave it a repair turn, and both worked. They
were just almost never reached: twenty-five files wrote `ticket.blocking_issues`
and three call sites — all in `run_completion` — offered the repair. The
reconciler's sweep then backfilled a kind onto everything else, which satisfied
"every block has a kind" while nothing had been done about any of them. A block
written by one of the other twenty-two files was indistinguishable, on the
board, from one that had been repaired (802).

So the two halves are no longer separately callable. `record_block` and
`offer_repair` have exactly one caller each — this module — and
`test_block_settlement` fails any other, checking calls as well as imports.
They are not spelled with a leading underscore because the `py-organization`
gate rejects a cross-module private import, which would have traded one gate
for another. `settle_block` guarantees the pair: a kind, and then either a
repair turn or a recorded `REPAIR_ESCALATED` naming why there was none.
"Classified" can no longer read as "handled", because a block that nothing
handled says so in the same history rail the repair would have appeared in.

The escalation is recorded here rather than inside `offer_repair` so there is
exactly one decision per block no matter which refusal applied.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import (
    BlockKind,
    OrchestrationRun,
    OrchestratorDecision,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.block_classification import block_message_for, record_block
from loregarden.services.block_repair import RepairOffer, offer_repair
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.run_concurrency import find_active_run
from sqlmodel import Session, select


@dataclass(frozen=True)
class RepairPolicy:
    """Whether a block may take its repair turn, and when not, the true reason.

    Required wherever a block is recorded outside a run's own completion, so a
    new blocking path cannot fall back to a reason that is not its own. Before
    this, every such path was settled with no orchestration and recorded "the
    block was recorded outside an orchestration run" — 56 of 93 escalations over
    the 30 days to 2026-10-14, most of them inside a live orchestration, blocked
    at a rework cap or a dispatch budget for reasons that sentence did not name.
    """

    withheld_reason: str = ""

    @classmethod
    def offer(cls) -> RepairPolicy:
        return cls()

    @classmethod
    def withheld(cls, reason: str) -> RepairPolicy:
        if not reason:
            raise ValueError("a withheld repair needs the reason a person will read")
        return cls(reason)

    @property
    def may_repair(self) -> bool:
        return not self.withheld_reason


#: An agent blocking its own ticket (`loregarden_block_ticket`, or the callback
#: API) while its run is still going: it asked for a person, and a repair turn
#: armed now would race the run that is asking.
AGENT_ASKED_FOR_A_PERSON = RepairPolicy.withheld(
    "An agent blocked this ticket itself while its run was still going; it asked for a "
    "person, and a repair turn would race that run."
)


@dataclass(frozen=True)
class BlockSettlement:
    """What became of a block: its kind, and whether an agent is about to try it."""

    kind: BlockKind
    repair_armed: bool
    reason: str = ""


def settle_block(
    session: Session,
    ticket: Ticket,
    orch_run: OrchestrationRun | None = None,
    *,
    stage_key: str,
    message: str,
    instance: WorkflowInstance | None = None,
    stages: list[WorkflowStageDef] | None = None,
    transitions: list[dict[str, str]] | None = None,
    failed_agent: str = "",
    declared: BlockKind | None = None,
    options: list[str] | None = None,
    kind_as_written: str = "",
    repairable: bool = True,
    no_repair_reason: str = "",
) -> BlockSettlement:
    """Classify this block and spend its repair turn, or record why it got none.

    Every path that leaves a ticket blocked calls this. The caller has already
    written `blocking_issues` and the BLOCKED stage status; this decides what
    happens to the block, and a re-armed stage goes back to PENDING under the
    repair agent.

    `repairable=False` is for a block whose cause no agent turn can touch — a
    provider quota window is the live case. It still gets its kind and its
    recorded reason; it just does not burn a turn proving the obvious.
    `no_repair_reason` is that reason in the caller's words; the default is the
    quota-window sentence.
    """
    kind = record_block(
        session,
        ticket,
        stage_key=stage_key,
        message=message,
        declared=declared,
        options=options,
        kind_as_written=kind_as_written,
    )
    if repairable:
        offer = offer_repair(
            session,
            ticket,
            orch_run,
            instance=instance,
            stages=stages,
            transitions=list(transitions or []),
            stage_key=stage_key,
            kind=kind,
            message=message,
            failed_agent=failed_agent,
        )
    else:
        offer = RepairOffer(
            False,
            no_repair_reason
            or (
                f"The block on '{stage_key}' is one no agent turn can clear — waiting it out "
                "is the fix — so no repair was offered."
            ),
        )
    if offer.armed:
        return BlockSettlement(kind, True)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.REPAIR_ESCALATED,
        stage_key=stage_key,
        reason=offer.reason,
        evidence={
            "block_kind": kind.value,
            "failed_agent": failed_agent,
            "block_message": message,
        },
    )
    session.commit()
    return BlockSettlement(kind, False, offer.reason)


def sweep_unclassified_blocks(session: Session) -> int:
    """Settle every blocked ticket the writers did not — run by the reconciler.

    It has no live orchestration to spend a repair turn on by definition: it is
    a reconciler pass over tickets whose runs are already over. That is exactly
    why it must go through `settle_block` rather than classifying alone — each
    ticket it reaches records a `REPAIR_ESCALATED` saying the block is waiting
    for a person, instead of quietly acquiring a kind and looking dealt with.

    The reconciler reaches this as the `every_block_is_settled` invariant
    (`services.invariant_registry`), which also reports each ticket found here
    as a writer that bypassed `settle_block`.
    """
    return settle_unclassified_blocks(session, unclassified_blocks(session))


def unclassified_blocks(session: Session) -> list[Ticket]:
    """Blocked tickets with no kind and no run still going to settle them."""
    stale = session.exec(
        select(Ticket).where(Ticket.blocking_issues != "", Ticket.block_kind.is_(None))
    ).all()
    # A ticket whose run is still going is left to that run's completion, which
    # settles the block with its orchestration and may yet repair it.
    return [ticket for ticket in stale if find_active_run(session, ticket.id) is None]


def settle_unclassified_blocks(session: Session, tickets: list[Ticket]) -> int:
    """Settle each of ``tickets`` still blocked with no kind. Returns how many."""
    settled = 0
    for ticket in tickets:
        session.refresh(ticket)
        if not ticket.blocking_issues or ticket.block_kind is not None:
            continue
        settle_block(
            session,
            ticket,
            stage_key=ticket.workflow_stage_key or "",
            message=block_message_for(session, ticket),
        )
        settled += 1
    return settled
