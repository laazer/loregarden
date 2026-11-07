"""`loregarden_settle_criterion`: record how one acceptance criterion is answered for.

The verify stage records criteria met, naming the tests that prove them; the
check before the gate reruns those tests at HEAD (`services.criterion_gate`).
The spec stage records criteria delegated to another ticket. An operator may
record either.

Who may call it is checked against the run, as `loregarden_decide_finding`
is: an implementer that could mark its own criteria met would be grading its
own work.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from loregarden.agents.registry import SPEC_AGENT_ID, VERIFIER_AGENT_ID
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import (
    enum_string_prop,
    string_list_prop,
    string_prop,
    tool_schema,
)
from loregarden.models.domain import (
    AgentRun,
    CriterionDisposition,
    Ticket,
    UpdateTicketRequest,
)
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.criterion_ledger import record_delegation, settle_met
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

CRITERION_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": McpTool.SETTLE_CRITERION,
        "description": (
            "Record how one acceptance criterion of a ticket is answered for. A ticket cannot "
            "enter its gate until every criterion is settled. met (verify stage, or an "
            "operator): name the tests that prove it, repo-relative, as 'path::test_name' "
            "(pytest node id) or 'path::\"exact jest name\"'; they are rerun at HEAD before the "
            "gate. Use evidence_artifact_id instead only for a criterion a test cannot show. "
            "delegated (spec stage, or an operator): hand it to target_ticket_id, which is given "
            "the criterion's exact text and which the parent waits for. Settle by the "
            "criterion's exact current text. A reason is always required."
        ),
        "inputSchema": tool_schema(
            properties={
                "ticket_id": string_prop(
                    "The ticket whose criterion this is (UUID or external_id)."
                ),
                "criterion": string_prop("The criterion, word for word as the ticket stores it."),
                "disposition": enum_string_prop(
                    "How it is answered for.", [d.value for d in CriterionDisposition]
                ),
                "tests": string_list_prop("met: the tests that prove it, as 'path::name'."),
                "evidence_artifact_id": string_prop(
                    "met without a test: the evidence artifact (loregarden_attach_evidence) that shows it."
                ),
                "target_ticket_id": string_prop("delegated: the ticket that takes it on."),
                "reason": string_prop("How the tests prove it, or why it is the target's work."),
            },
            required=["ticket_id", "criterion", "disposition", "reason"],
        ),
    }
]

#: The agent each disposition belongs to, when a pipeline run records it.
_SETTLER = {
    CriterionDisposition.MET: VERIFIER_AGENT_ID,
    CriterionDisposition.DELEGATED: SPEC_AGENT_ID,
}


def _settler(
    session: Session,
    ticket: Ticket,
    disposition: CriterionDisposition,
    *,
    orchestrated: bool,
    run_id: str,
) -> str:
    """Who is settling, refused unless it is that disposition's stage on this ticket."""
    if not orchestrated:
        return "operator"
    run = session.get(AgentRun, run_id) if run_id else None
    owner = _SETTLER[disposition]
    if run is None or run.agent_id != owner or run.ticket_id != ticket.id:
        raise ValueError(
            f"A criterion is recorded {disposition.value} by the {owner} stage of its own "
            "ticket. Name it in your stage report instead (unmet_criteria)."
        )
    return f"run:{run.id}"


def _give_target(session: Session, target: Ticket, criterion: str) -> None:
    """Add the criterion to the target's own criteria, word for word, if it lacks it."""
    criteria = load_criteria(target.acceptance_criteria_json)
    if criterion in criteria:
        return
    OrchestrationService(session).update_ticket_manual(
        target, UpdateTicketRequest(acceptance_criteria=[*criteria, criterion])
    )
    session.refresh(target)


def settle_criterion_tool(
    session: Session, arguments: dict[str, Any], *, orchestrated: bool, run_id: str
) -> str:
    callbacks = OrchestrationCallbackService(session)
    ticket = callbacks.resolve_ticket(ticket_id=arguments["ticket_id"])
    disposition = CriterionDisposition(arguments["disposition"])
    settled_by = _settler(session, ticket, disposition, orchestrated=orchestrated, run_id=run_id)
    criterion = (arguments.get("criterion") or "").strip()
    reason = arguments.get("reason") or ""
    if disposition is CriterionDisposition.MET:
        row = settle_met(
            session,
            ticket,
            criterion,
            tests=list(arguments.get("tests") or []),
            evidence_artifact_id=arguments.get("evidence_artifact_id") or None,
            commit_sha=resolve_head_sha(session, ticket),
            settled_by=settled_by,
            reason=reason,
        )
    else:
        if not arguments.get("target_ticket_id"):
            raise ValueError("delegated needs target_ticket_id: the ticket that takes it on")
        target = callbacks.resolve_ticket(ticket_id=arguments["target_ticket_id"])
        _give_target(session, target, criterion)
        row = record_delegation(
            session, ticket, criterion, target=target, settled_by=settled_by, reason=reason
        )
        session.commit()
    return json.dumps(
        {
            "ticket": ticket.external_id,
            "criterion": row.criterion,
            "disposition": row.disposition.value,
            "settled_by": row.settled_by,
        },
        indent=2,
    )
