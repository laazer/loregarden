"""Split an oversized ticket into children before its pipeline starts.

A ticket carrying dozens of criteria runs every stage over a prompt that only
grows, and one reviewer finding on one corner of it sends the whole ticket back
through implement. lg-durable-remote-336 (38 criteria, a 28k-char description)
reached 132k-char prompts and bounced on three tmux criteria while the rest was
done. Split, each part ships on its own.

The split happens server-side because orchestrated agents are denied
``loregarden_create_ticket``. Once children exist the builtin orchestrator
already treats the parent as an aggregator: it runs each child, then finalizes
the parent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial

from loregarden.models.domain import (
    VALID_HIERARCHY,
    ArtifactKind,
    StageStatus,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.decomposition_service import DecompositionService, GenerateFn
from loregarden.services.finalize_hierarchy import finalize_hierarchy
from loregarden.services.integration_review import ensure_review_child
from loregarden.services.proposal_validator import ProposalValidationError
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

# Measured 2026-10-09: 40 of ~1,280 tickets exceed one or the other.
MAX_ACCEPTANCE_CRITERIA = 15
MAX_DESCRIPTION_CHARS = 15_000

SPLIT_CREATED_BY = "oversized-ticket-split"

TICKET_SPLIT_CLI_PROFILE = CliAgentProfile(
    agent_id="planner",
    assistant_label="Ticket split assistant",
    cli_label="Ticket split",
    stub_env="LOREGARDEN_TICKET_SPLIT_STUB_RESPONSE",
    timeout_env="LOREGARDEN_TICKET_SPLIT_TIMEOUT",
    tmp_prefix="loregarden-ticket-split-",
    # Children carry the parent's criteria and description verbatim between them.
    reply_cap=80_000,
)


class SplitOutcome(StrEnum):
    NOT_NEEDED = "not_needed"
    SPLIT = "split"
    UNSPLITTABLE = "unsplittable"
    FAILED = "failed"


@dataclass(frozen=True)
class SplitResult:
    outcome: SplitOutcome
    detail: str = ""
    child_ids: list[str] = field(default_factory=list)


def is_oversized(ticket: Ticket) -> bool:
    return (
        len(load_criteria(ticket.acceptance_criteria_json)) > MAX_ACCEPTANCE_CRITERIA
        or len(ticket.description or "") > MAX_DESCRIPTION_CHARS
    )


def split_child_type(parent_type: WorkItemType) -> WorkItemType | None:
    """The type a split's children take: the parent's first non-bug child type."""
    return next((t for t in VALID_HIERARCHY[parent_type] if t != WorkItemType.BUG), None)


def split_oversized_ticket(
    session: Session,
    ticket: Ticket,
    callbacks,
    *,
    generate: GenerateFn | None = None,
) -> SplitResult:
    """Split ``ticket`` into children when it is oversized and nothing has run yet.

    Anything short of a split is recorded on the ticket and the ticket runs
    unsplit — the split saves time, and its absence costs only that.
    """
    if not _eligible(session, ticket):
        return SplitResult(SplitOutcome.NOT_NEEDED)
    child_type = split_child_type(ticket.work_item_type)
    if child_type is None:
        result = SplitResult(
            SplitOutcome.UNSPLITTABLE,
            f"A {ticket.work_item_type.value} cannot hold child tickets.",
        )
    else:
        result = _split(session, ticket, child_type, generate)
    _record(session, callbacks, ticket, result)
    return result


def _eligible(session: Session, ticket: Ticket) -> bool:
    if ticket.is_integration_review or not is_oversized(ticket):
        return False
    has_children = session.exec(
        select(Ticket.id).where(Ticket.parent_ticket_id == ticket.id).limit(1)
    ).first()
    return has_children is None and _pipeline_untouched(session, ticket)


def _pipeline_untouched(session: Session, ticket: Ticket) -> bool:
    """True while every stage is still pending — a ticket mid-flight is never split."""
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return True
    _, stages = resolve_ticket_stages(session, ticket)
    return all(
        status == StageStatus.PENDING for status in parse_stage_map(instance, stages).values()
    )


def _split(
    session: Session,
    ticket: Ticket,
    child_type: WorkItemType,
    generate: GenerateFn | None,
) -> SplitResult:
    service = DecompositionService(generate or partial(_invoke_split_model, session, ticket))
    content = {
        "title": ticket.title,
        "description": ticket.description or "",
        "acceptance_criteria": load_criteria(ticket.acceptance_criteria_json),
    }
    try:
        children = service.split(content, child_type=child_type)
        child_ids = _persist(session, ticket, children)
    except (ValueError, ProposalValidationError, RuntimeError, TimeoutError, OSError) as exc:
        session.rollback()
        logger.warning("Splitting oversized ticket %s failed: %s", ticket.external_id, exc)
        return SplitResult(SplitOutcome.FAILED, str(exc))
    return SplitResult(
        SplitOutcome.SPLIT, f"Split into {len(child_ids)} {child_type.value} tickets.", child_ids
    )


def _persist(session: Session, ticket: Ticket, children: list[HierarchyWorkItem]) -> list[str]:
    """Create the children under ``ticket``, chained so they run in proposal order."""
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        raise ValueError(f"Workspace not found for ticket {ticket.external_id}")
    # Refs derive from the parent so a model-chosen slug cannot collide with a
    # live ticket; they become the children's legacy ids.
    for index, child in enumerate(children, start=1):
        child.external_id = f"{ticket.external_id}-part-{index}"
        child.parent_ticket_id = ticket.id
    child_ids = finalize_hierarchy(session, workspace_slug=workspace.slug, hierarchy=children)
    dependencies = TicketDependencyService(session)
    for earlier, later in zip(child_ids, child_ids[1:], strict=False):
        dependencies.add_dependency(later, earlier, created_by=SPLIT_CREATED_BY)
    ensure_review_child(session, ticket, created_by=SPLIT_CREATED_BY)
    return child_ids


def _invoke_split_model(session: Session, ticket: Ticket, prompt: str) -> str:
    stub = stub_response(TICKET_SPLIT_CLI_PROFILE)
    if stub is not None:
        return stub
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        raise ValueError(f"Workspace not found for ticket {ticket.external_id}")
    return run_cli_agent_turn(
        TICKET_SPLIT_CLI_PROFILE,
        workspace=workspace,
        prompt=prompt,
        workspace_slug=workspace.slug,
        read_only=True,
    )


def _record(session: Session, callbacks, ticket: Ticket, result: SplitResult) -> None:
    """Put the outcome on the ticket's timeline, where a failed split stays visible."""
    session.refresh(ticket)
    title = (
        f"Split oversized ticket — {result.detail}"
        if result.outcome is SplitOutcome.SPLIT
        else "Oversized ticket running unsplit"
    )
    rows = [
        {"k": "Outcome", "v": result.outcome.value},
        {"k": "Detail", "v": result.detail},
        {
            "k": "Threshold",
            "v": f"> {MAX_ACCEPTANCE_CRITERIA} criteria or > {MAX_DESCRIPTION_CHARS:,} description chars",
        },
    ]
    callbacks.attach_artifact(
        ticket, kind=ArtifactKind.CONTEXT, title=title, content={"title": title, "rows": rows}
    )
