"""Pin the agent a ticket's next dispatch of a stage must use.

The pin already existed — `tickets.scope_reroute_agent`, set by the permission
bridge when a scoped implementer is denied a cross-scope write — and classify
routing honours it above its own keyword scoring, consuming it at dispatch. What
did not exist was a way for a person to set it.

Observed on the first live run of the merge process (lg-milestone-that-717):
the classify stage scored a backend ticket to `frontend_implementer`, which did
what it could, committed nothing, and reported "re-run with backend_implementer"
— and the next classify scored it to `frontend_implementer` again, at ~500K
input tokens a pass, on its way to the rework cap. Nothing consumed the report's
handoff, and the only way to steer the next dispatch was a hand-written SQL
update.
"""

from __future__ import annotations

import json

from loregarden.agents.registry import get_agent
from loregarden.models.domain import Artifact, ArtifactKind, Ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.studio_routing import resolve_scope_reroute_pin
from sqlmodel import Session


def pin_stage_agent(
    session: Session,
    orch: OrchestrationService,
    ticket: Ticket,
    *,
    agent_id: str,
    reason: str,
    stage_key: str = "",
    actor: str,
) -> str:
    """Pin ``agent_id`` for ``ticket``'s next dispatch of ``stage_key``. Returns the stage.

    Refuses an agent the stage cannot run — a pin that no route offers would
    never be consumed and would sit on the ticket forever — and an empty
    reason, because the next reader finds the pin and has to know why.
    """
    if not reason.strip():
        raise ValueError("A reason is required — it is the record of why routing was overridden.")
    if get_agent(agent_id) is None:
        raise ValueError(f"Unknown agent {agent_id!r}")
    key = (stage_key or ticket.workflow_stage_key or "").strip()
    if not key:
        raise ValueError("The ticket has no current stage; pass stage_key.")
    instance, stages = orch._resolve_stages(ticket)
    stage = next((s for s in stages if s.key == key), None) if instance and stages else None
    if stage is None:
        raise ValueError(f"Stage {key!r} is not in this ticket's workflow")

    previous = ticket.scope_reroute_agent
    ticket.scope_reroute_agent = agent_id
    if resolve_scope_reroute_pin(ticket, stage) is None:
        ticket.scope_reroute_agent = previous
        offered = sorted(
            {route.agent_id for route in stage.classify_routes} | {stage.agent_id} - {""}
        )
        raise ValueError(
            f"Stage {key!r} cannot run {agent_id!r}; it offers {', '.join(offered) or 'nothing'}"
        )

    ticket.revision += 1
    ticket.last_updated_by = actor
    session.add(ticket)
    session.add(
        Artifact(
            ticket_id=ticket.id,
            kind=ArtifactKind.CONTEXT,
            title=f"Pinned {agent_id} — {key}",
            content_json=json.dumps(
                {
                    "title": f"Pinned {agent_id} — {key}",
                    "rows": [
                        {"k": "Stage", "v": key},
                        {"k": "Agent", "v": agent_id},
                        {"k": "Reason", "v": reason},
                    ],
                }
            ),
        )
    )
    session.commit()
    return key
