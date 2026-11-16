"""One ticket's workflow position, edges and active run, as plain JSON.

There is one shape and one builder, whichever transport asks. REST once kept
a hand-built copy of it: the copy lost `revision` and `current_stage_shape`,
and kept committing through `build_stage_views` on a GET after the MCP read
tools had stopped. Whether a call writes is the caller's choice of
`stage_views`, never a second copy of the payload.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loregarden.models.domain import Ticket, WorkflowStageView
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.dependency_readiness import unmet_prerequisites_for_start
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.stage_shape import current_stage_shape
from loregarden.services.studio_routing import ticket_stage_agent
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_discovery import ticket_neighbors_mcp
from loregarden.services.ticket_documents import document_index
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_tags import load_tags
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from sqlmodel import Session


def ticket_state_payload(session: Session, ticket_id: str) -> dict[str, Any]:
    """The payload after a write: reconciles and persists the stage map on the way.

    Right for a write and wrong for a read; a read takes `ticket_state_for_read`.
    """
    ticket = OrchestrationCallbackService(session).resolve_ticket(ticket_id=ticket_id)
    return state_payload(session, ticket, OrchestrationService(session).build_stage_views)


def ticket_state_for_read(session: Session, ticket: Ticket) -> dict[str, Any]:
    """The payload for a read: the stage map is derived, and nothing is committed.

    Through `build_stage_views` every read committed the ticket and its workflow
    instance, moving `updated_at` with no event behind it, and gave a ticket
    with no instance one as a side effect (lg-workflow-integrity-606).
    """
    return state_payload(session, ticket, OrchestrationService(session).stage_views_for_read)


def state_payload(
    session: Session,
    ticket: Ticket,
    stage_views: Callable[[Ticket], list[WorkflowStageView]],
) -> dict[str, Any]:
    """The payload every caller returns; `stage_views` decides whether it writes."""
    active = OrchestrationCallbackService(session).get_active_orchestration_run(ticket.id)
    return {
        "ticket_id": ticket.id,
        "external_id": ticket.external_id,
        # Every write gets this payload, so the revision is here
        # unconditionally: an agent cannot send `expected_revision` on its next
        # call if the key only appears when it sent one on this call.
        "revision": ticket.revision,
        # The id this ticket read as before the restructure, so an agent working
        # from an older artifact or branch name can tell it is the same ticket.
        "legacy_external_id": ticket.legacy_external_id,
        "state": ticket.state.value,
        "workflow_stage_key": ticket.workflow_stage_key,
        "workflow_stage_status": ticket.workflow_stage_status.value,
        "current_stage_agent": ticket_stage_agent(session, ticket),
        "current_stage_shape": current_stage_shape(session, ticket),
        "blocking_issues": ticket.blocking_issues,
        "active_orchestration": (
            {
                "id": active.id,
                "run_code": active.run_code,
                "status": active.status.value,
                "driver": active.driver.value,
                "current_stage_key": active.current_stage_key,
            }
            if active
            else None
        ),
        "stages": [s.model_dump() for s in stage_views(ticket)],
        "tags": load_tags(ticket.tags_json),
        "hierarchy": ticket_neighbors_mcp(session, ticket),
        "depends_on": ticket_summaries(
            session, TicketDependencyService(session).prerequisites(ticket.id)
        ),
        "dependents": ticket_summaries(
            session, TicketDependencyService(session).dependents(ticket.id)
        ),
        "related": ticket_summaries(session, TicketRelationService(session).related(ticket.id)),
        # The subset of `depends_on` that is actually holding this ticket up.
        # `depends_on` alone does not answer "can this start?" — a reader has to
        # check each far end's state, and for an edge into another workspace
        # they cannot see that board at all (676).
        "blocked_by": blocked_by_summaries(session, ticket),
    }


def blocked_by_summaries(session: Session, ticket: Ticket) -> list[dict[str, str]]:
    """`blocked_by`, each entry saying why: not done, or done but not landed (770)."""
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return ticket_summaries(
            session,
            [t.id for t in TicketDependencyService(session).unmet_prerequisites(ticket.id)],
        )
    unmet = unmet_prerequisites_for_start(session, ticket, workspace)
    by_id = {entry.ticket.id: entry for entry in unmet}
    summaries = ticket_summaries(session, list(by_id))
    for summary in summaries:
        entry = by_id[summary["id"]]
        summary["reason"] = entry.reason.value
        summary["needs_landing_on"] = entry.target
    return summaries


def ticket_summaries(session: Session, ticket_ids: list[str]) -> list[dict[str, str]]:
    """The far end of an edge, in the shape both dependencies and relations use.

    Carries the workspace because the far end need not be in this one. Nine such
    edges already existed when this was written, including a chain running both
    ways between loregarden and lore-eden, and an external id on its own sends
    the reader to the wrong board (676).
    """
    out: list[dict[str, str]] = []
    for tid in ticket_ids:
        dep = session.get(Ticket, tid)
        if dep is not None:
            workspace = workspace_for_ticket(session, dep)
            out.append(
                {
                    "id": dep.id,
                    "external_id": dep.external_id,
                    "workspace": workspace.slug if workspace is not None else "",
                    "title": dep.title,
                    "state": dep.state.value,
                }
            )
    return out


def ticket_body(ticket: Ticket) -> dict[str, Any]:
    """What the ticket *asks for*, as against where it sits in the pipeline.

    Empty is a real answer here. A ticket with no description returns ``""`` and
    no criteria returns ``[]`` rather than omitting the keys: an agent that can
    tell "nobody wrote a requirement" from "I cannot see the requirement" will
    ask instead of inventing one, and inventing one is the failure this exists
    to stop — the invention lands in a test docstring at test-design and every
    later stage treats it as the spec.
    """
    return {
        "title": ticket.title,
        "description": ticket.description,
        "acceptance_criteria": load_criteria(ticket.acceptance_criteria_json),
        # What the spec ruled out, with reasons: a finding on one of these is
        # not new scope unless the reason is wrong.
        "out_of_scope": load_criteria(ticket.out_of_scope_json),
    }


def ticket_brief(session: Session, ticket: Ticket) -> dict[str, Any]:
    """What an agent reads before acting: the ticket's position *and* its requirement.

    The body rides on the brief rather than on
    ``ticket_state_payload``, which every write tool also returns. Those are two
    different questions asked at two different moments: a `complete_stage` reply
    is asking "where am I now", and answering it with a thousand-word
    description would put the whole requirement back into context on every
    write. A read is where "what am I meant to do" is actually being asked.

    Widening the brief is preferred over a separate `get_ticket_body` tool
    because the failure being fixed is an agent not knowing the body exists at
    all — a second tool it must first think to call would reproduce that, and a
    `fields=` parameter has the same defect with more surface.
    """
    # Planning documents on this ticket and every ancestor up to its initiative,
    # for the same reason the body rides here: an agent that is not told they
    # exist will not think to list them. Read one with loregarden_read_artifact.
    return (
        # A read, so the stage map is derived and not persisted; that once read
        # as a state change at 19:26 on lg-milestone-that-2196.
        ticket_state_for_read(session, ticket)
        | ticket_body(ticket)
        | {"documents": document_index(session, ticket)}
    )
