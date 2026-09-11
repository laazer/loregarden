"""What `OrchestrationService.start_run` settles before it writes anything.

Four decisions with one shape: each reads the ticket and the stage definition,
answers a question the dispatch cannot proceed without, and — for the three pins
— spends the one-shot request that asked for it. They lived in `orchestration`
until it went over its line cap; the seam is the one already there, since
`start_run` calls all four in a row and nothing else calls any of them.

The pins share an invariant worth stating once, because each docstring below
restates a corner of it: a pin is a request about ONE dispatch, and it is
consumed only by a dispatch that *satisfied* it. A pin the dispatch did not match
stands, rather than being silently spent on the wrong stage or the wrong agent.
"""

from __future__ import annotations

from loregarden.models.domain import Ticket, WorkflowStageDef
from loregarden.services.studio_routing import (
    is_agentless_stage,
    resolve_stage_execution,
    unrouteable_classify_detail,
)


def resolve_run_agent(
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    *,
    agent_id: str | None,
    skill_name: str | None,
) -> tuple[str, str | None]:
    """Which agent (and skill) runs this stage, or why nothing can.

    Explicit ``agent_id``/``skill_name`` win over what the stage resolves to —
    that is how a driver fans a parallel stage out one member at a time.
    """
    resolved_agent_id, resolved_skill = resolve_stage_execution(ticket, stage_def)
    chosen_agent = agent_id or resolved_agent_id
    chosen_skill = skill_name or resolved_skill or stage_def.skill_name
    if is_agentless_stage(stage_def):
        raise ValueError(
            f"Stage '{stage_def.key}' is a human approval gate — it does not run an agent CLI."
        )
    if not chosen_agent:
        # A classify stage whose roster has nobody for this ticket says so in
        # its own words. The generic message below sent readers looking for a
        # fan-out bug when the fault was a template missing a specialty — and
        # before this refused at all, the stage dispatched the least-wrong
        # agent, which declined the work and exited `succeeded` (ClassifyBasis).
        unrouteable = unrouteable_classify_detail(ticket, stage_def)
        if unrouteable:
            raise ValueError(
                f"Stage '{stage_def.key}' has no route for this ticket: {unrouteable}."
            )
        # Two different faults used to share the gate message above, which
        # sent every reader looking for a gate that was not there. A stage
        # that *should* run an agent but resolved none is a routing defect —
        # most often a parallel stage started without naming which member
        # this run is (`agent_id`), since its agents live in
        # `parallel_agents` and only a driver can fan them out.
        raise ValueError(
            f"Stage '{stage_def.key}' resolved no agent to run it. A "
            f"'{stage_def.stage_type}' stage must either name an agent or be "
            "started per member with an explicit agent_id."
        )
    return chosen_agent, chosen_skill


def consume_scope_reroute_pin(ticket: Ticket, chosen_agent: str) -> None:
    """Clear the scope-denial reroute pin once its dispatch is committed.

    The pin exists to steer this one dispatch to the sibling implementer; clearing
    it here means a *fresh* denial in this run sets a new pin, but a satisfied one
    can never linger into a later stage as a stale hint.
    """
    if ticket.scope_reroute_agent and ticket.scope_reroute_agent == chosen_agent:
        ticket.scope_reroute_agent = ""


def consume_dispatch_waiver(ticket: Ticket, stage_key: str) -> str:
    """Take the one-shot pre-dispatch waiver for `stage_key`, or "" if none.

    Armed by approving a parked stage (`ApprovalService._apply_park_resolution`)
    and cleared here, so a person's "run it anyway" covers exactly the dispatch
    it was clicked for. Without the clear, the waiver would follow the ticket
    into every later stage; without the waiver, the re-dispatched stage would
    trip the same check and park again, which is the loop that makes "approve"
    useless rather than merely wrong.

    Stage-scoped for the same reason `consume_scope_reroute_pin` is agent-scoped:
    a waiver the dispatch did not match is a request that has not been satisfied,
    so it stands rather than being silently spent on the wrong stage.
    """
    if not ticket.dispatch_waiver_stage_key or ticket.dispatch_waiver_stage_key != stage_key:
        return ""
    approval_id = ticket.dispatch_waiver_approval_id
    ticket.dispatch_waiver_stage_key = ""
    ticket.dispatch_waiver_approval_id = ""
    return approval_id


def consume_next_agent_pin(ticket: Ticket, chosen_agent: str) -> None:
    """Clear the routing hint once the dispatch it asked for has happened.

    `next_agent` is written by a reject to send work back to a named agent
    (`workflow_routing.apply_stage_route`). It is a request about ONE dispatch,
    and it was persisted as though it were a standing fact — so a hint set at
    `verify` was still steering three stages later, which is the stale-pin loop
    #164 documented.

    Cleared only when the dispatch actually went to the pinned agent, matching
    `consume_scope_reroute_pin` above. A dispatch that resolved somewhere else
    did not satisfy the request, so the request stands.

    This only works because the read path stopped rewriting the field. It used
    to be restored by `reconcile_workflow_state` on the next
    `GET /api/tickets/{id}`, so clearing here would have been undone before the
    stage after it ran.
    """
    if ticket.next_agent and ticket.next_agent == chosen_agent:
        ticket.next_agent = ""
