"""A settling stage may not report pass with a criterion still unsettled.

The check before the gate (`criterion_gate`) sends a gap back to the stage
that settles criteria as a new run. When the only gaps are criteria that
stage never settled, that run exists to call `loregarden_settle_criterion`:
bookkeeping the agent that just reported pass could have done in the same
context. So the pass is refused while the agent is still live, naming each
unsettled criterion in the same words the gate's brief uses.

This only asks whether a settlement exists (`criterion_ledger.unsettled`).
Nothing is rerun here: the gate's own check still reruns the tests at HEAD
and still catches a settlement that went stale.

A `reject` (or a `fail`/`needs_rework` report) is never refused: an agent
sending the work back owes no settlements.
"""

from __future__ import annotations

from loregarden.mcp.tool_ids import McpTool
from loregarden.models.domain import Ticket
from loregarden.services.criterion_gate_stages import settler_of
from loregarden.services.criterion_ledger import (
    CriterionGap,
    GapKind,
    unanswered_list,
    unsettled,
)
from loregarden.services.workflow_service import resolve_ticket_stages
from sqlmodel import Session


class UnsettledCriteriaAtPass(ValueError):
    """A settling stage reported pass with criteria unsettled.

    A ValueError so both tool boundaries already turn it into an ``isError``
    result carrying this message, with nothing written.
    """


def unsettled_pass_message(
    session: Session, ticket: Ticket, stage_key: str, *, then: str
) -> str | None:
    """Why a pass from ``stage_key`` cannot stand, or None when it can.

    None unless ``stage_key`` is the stage that settles ``ticket``'s criteria
    and one is unsettled. ``then`` is what the agent does once it has settled
    them ("call loregarden_complete_stage again"). The caller has already
    established that the stage is reporting pass.
    """
    _, stages = resolve_ticket_stages(session, ticket)
    settler = settler_of(stages)
    if settler is None or settler.key != stage_key:
        return None
    missing = unsettled(session, ticket)
    if not missing:
        return None
    gaps = [CriterionGap(text, GapKind.UNSETTLED) for text in missing]
    return (
        f"'{stage_key}' cannot pass yet: {unanswered_list(ticket, gaps)}\n\n"
        f"For each, call {McpTool.SETTLE_CRITERION.value} with ticket_id "
        f"{ticket.external_id}, the criterion word for word, disposition met, "
        "tests naming the tests that prove it as 'path::test_name' (they are rerun "
        "at HEAD before the gate) or evidence_artifact_id for one a test cannot "
        f"show, and a reason. Then {then}. A criterion that is not met is not "
        "settled: report reject (needs_rework) instead, naming it in unmet_criteria."
    )


def refuse_pass_with_unsettled(
    session: Session, ticket: Ticket, stage_key: str, *, retry_tool: McpTool
) -> None:
    """Raise if ``stage_key`` settles ``ticket``'s criteria and any is unsettled.

    ``retry_tool`` is the call the agent makes again once it has settled them.
    """
    message = unsettled_pass_message(
        session, ticket, stage_key, then=f"call {retry_tool.value} again"
    )
    if message is not None:
        raise UnsettledCriteriaAtPass(message)
