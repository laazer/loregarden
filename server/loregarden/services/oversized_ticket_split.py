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

It is asked at every stage boundary, not once before the first stage, because
the stages that write a ticket's criteria (plan, spec) run after triage:
lg-external-harness-1026 entered triage with a handful and left spec with 34.
The last moment to split is before any stage commits to the ticket's branch —
after that, a split would strand the work on a branch no child runs on.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial

from loregarden.models.domain import (
    VALID_HIERARCHY,
    ArtifactKind,
    DomainEvent,
    EventType,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.schemas import HierarchyWorkItem
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    run_cli_agent_turn,
    stub_response,
)
from loregarden.services.decomposition_service import DecompositionService, GenerateFn
from loregarden.services.finalize_hierarchy import finalize_hierarchy
from loregarden.services.git_merge_noco import read_branch_tips
from loregarden.services.git_subprocess import run_git
from loregarden.services.integration_review import ensure_review_child
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.proposal_validator import ProposalValidationError
from loregarden.services.target_branch import TargetBranchError, target_branch_name
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlalchemy import func
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
    #: Oversized, but the ticket's branch already carries work (or whether it
    #: does could not be read), so splitting would strand it.
    WORK_STARTED = "work_started"


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
    """Split ``ticket`` into children when it is oversized and no work has started.

    Called at every stage boundary. Anything short of a split is recorded on
    the ticket — an artifact and a decision on its history rail — and the ticket
    runs unsplit; the same content is never tried twice, so the record is made
    once and the model is not re-invoked at every boundary.
    """
    if (
        ticket.is_integration_review
        or not is_oversized(ticket)
        or _has_children(session, ticket)
        or not _at_stage_boundary(session, ticket)
    ):
        return SplitResult(SplitOutcome.NOT_NEEDED)
    digest = content_digest(ticket)
    if _already_ran_unsplit(session, ticket, digest):
        return SplitResult(SplitOutcome.NOT_NEEDED, "Already recorded running unsplit.")
    child_type = split_child_type(ticket.work_item_type)
    started = _work_started(session, ticket)
    if started:
        result = SplitResult(SplitOutcome.WORK_STARTED, started)
    elif child_type is None:
        result = SplitResult(
            SplitOutcome.UNSPLITTABLE,
            f"A {ticket.work_item_type.value} cannot hold child tickets.",
        )
    else:
        result = _split(session, ticket, child_type, generate)
    _record(session, callbacks, ticket, result, digest)
    return result


def content_digest(ticket: Ticket) -> str:
    """What the split would be asked to divide, so a changed ticket is asked again."""
    content = [
        ticket.title,
        ticket.description or "",
        load_criteria(ticket.acceptance_criteria_json),
    ]
    return hashlib.sha256(json.dumps(content).encode()).hexdigest()


def _has_children(session: Session, ticket: Ticket) -> bool:
    return (
        session.exec(select(Ticket.id).where(Ticket.parent_ticket_id == ticket.id).limit(1)).first()
        is not None
    )


def _stage_statuses(session: Session, ticket: Ticket) -> list[StageStatus]:
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return []
    _, stages = resolve_ticket_stages(session, ticket)
    return list(parse_stage_map(instance, stages).values())


def _at_stage_boundary(session: Session, ticket: Ticket) -> bool:
    """False while a stage is running or awaiting sign-off.

    An aggregator parent is finalized only from a settled stage
    (`finalize_aggregator_ticket` leaves RUNNING/AWAITING alone), so a split
    taken mid-stage would leave the parent unable to close. The next boundary
    asks again.
    """
    return not any(
        status in (StageStatus.RUNNING, StageStatus.AWAITING)
        for status in _stage_statuses(session, ticket)
    )


def _already_ran_unsplit(session: Session, ticket: Ticket, digest: str) -> bool:
    found = session.exec(
        select(DomainEvent.id)
        .where(DomainEvent.ticket_id == ticket.id)
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
        .where(
            func.json_extract(DomainEvent.payload_json, "$.decision")
            == OrchestratorDecision.RAN_OVERSIZED_UNSPLIT.value
        )
        .where(func.json_extract(DomainEvent.payload_json, "$.content_digest") == digest)
        .limit(1)
    ).first()
    return found is not None


def _work_started(session: Session, ticket: Ticket) -> str:
    """Why a split would strand work, or "" when there is none to strand.

    Every stage still pending is the cheap answer. Past that, the question is
    whether the ticket's branch holds a commit of its own — one neither its
    target nor the base reaches, merges aside, since a merge of the target in
    adds nothing a child would lose. Plan and spec commit nothing; test-design
    is usually the first stage that does. A branch that cannot be read answers
    as started: an unprovable "nothing to lose" is not a satisfied one.
    """
    if all(status == StageStatus.PENDING for status in _stage_statuses(session, ticket)):
        return ""
    workspace = session.get(Workspace, ticket.workspace_id)
    if workspace is None:
        return f"Workspace not found for ticket {ticket.external_id}."
    branch = resolve_ticket_branch(ticket)
    try:
        total = _own_commit_count(session, ticket, workspace, branch)
    except (TargetBranchError, ValueError, OSError) as exc:
        return f"Could not tell whether {branch} carries work: {exc}"
    if total == 0:
        return ""
    return f"{branch} already carries {total} commit(s) of its own, which a split would strand."


def _own_commit_count(session: Session, ticket: Ticket, workspace: Workspace, branch: str) -> int:
    """Non-merge commits on ``branch`` that neither its target nor the base reach.

    0 when the branch does not exist yet. Raises when git cannot answer.
    """
    repo_root = resolve_workspace_root(workspace)
    target = target_branch_name(session, ticket, workspace)
    base = resolve_orchestration_profile(workspace).git.base_branch
    tips = read_branch_tips(repo_root, branch, target, base)
    if tips.error:
        raise ValueError(tips.error)
    tip = tips.of(branch)
    if not tip:
        return 0
    reached = sorted({tips.of(target), tips.of(base)} - {""})
    counted = run_git(
        ["rev-list", "--count", "--no-merges", tip, "--not", *reached],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    total = counted.stdout.strip()
    if counted.returncode != 0 or not total.isdigit():
        raise ValueError((counted.stderr or counted.stdout or "git rev-list failed").strip())
    return int(total)


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


def _record(session: Session, callbacks, ticket: Ticket, result: SplitResult, digest: str) -> None:
    """Put the outcome on the ticket's timeline and history rail, where a ticket
    running unsplit stays visible."""
    session.refresh(ticket)
    split = result.outcome is SplitOutcome.SPLIT
    title = (
        f"Split oversized ticket — {result.detail}" if split else "Oversized ticket running unsplit"
    )
    threshold = (
        f"> {MAX_ACCEPTANCE_CRITERIA} criteria or > {MAX_DESCRIPTION_CHARS:,} description chars"
    )
    rows = [
        {"k": "Outcome", "v": result.outcome.value},
        {"k": "Detail", "v": result.detail},
        {"k": "Threshold", "v": threshold},
    ]
    callbacks.attach_artifact(
        ticket, kind=ArtifactKind.CONTEXT, title=title, content={"title": title, "rows": rows}
    )
    record_orchestrator_decision(
        session,
        ticket,
        decision=(
            OrchestratorDecision.SPLIT_OVERSIZED_TICKET
            if split
            else OrchestratorDecision.RAN_OVERSIZED_UNSPLIT
        ),
        stage_key=ticket.workflow_stage_key,
        reason=(
            result.detail
            if split
            else f"Over the size threshold ({threshold}) and running unsplit: {result.detail}"
        ),
        evidence={
            "outcome": result.outcome.value,
            "child_ids": result.child_ids,
            "content_digest": digest,
        },
    )
