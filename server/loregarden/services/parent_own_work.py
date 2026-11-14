"""Does a parent hold work of its own that landed nowhere? Asked before it is closed.

A parent is closed as its children's summary: the rollup derives `done` once
they resolve, and the aggregator finalise forces its stages shut. Both assume
a parent carries no work of its own, and a ticket that ran its own stages and
then had tickets filed under it breaks that. A review lane's finding, a ticket
an agent created under it, or a person's split all make it a parent, and its
gate and landing never run again. lg-milestone-that-1429 was closed that way at
its gate with 31 files on no branch but its own; lg-milestone-that-1216,
lg-improved-memory-662 and lg-initiatives-cross-732 before it
(lg-milestone-that-2604).

Nothing in the database says reliably that a ticket wrote code: planning stages
record paths too, and agents create tickets with no finding row. The branch
does. So this asks git, and only at the moment of closing, which is rare: never
on a read.
"""

from __future__ import annotations

import logging

from loregarden.agents.registry import SPEC_AGENT_ID
from loregarden.models.domain import StageStatus, Ticket
from loregarden.models.domain.stage_types import StageType
from loregarden.services.branch_names import resolve_ticket_branch
from loregarden.services.git_automation_config import base_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.landing_carried_work import carries_own_work
from loregarden.services.target_branch import target_branch_name
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.work_landed import landing_commit
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Stages after the spec a ticket reaches only by running its own work: review lanes, verify.
_REVIEWED = (StageType.PARALLEL, StageType.VERIFY)
_SETTLED = (StageStatus.DONE, StageStatus.WONT_DO)


def own_gate_pending(session: Session, ticket: Ticket) -> str:
    """Why ``ticket``'s own stages must not be forced shut, or "".

    A ticket that has been reviewed has work of its own whatever git says, and
    its gate is what checks it. lg-milestone-that-1420 sat at verify when a
    finding filed under it made it a parent, and the aggregator would have
    retired its gate unrun (lg-milestone-that-2604).
    """
    instance = workflow_instance_for(session, ticket.id)
    if instance is None:
        return ""
    _, stages = resolve_ticket_stages(session, ticket)
    spec = next((s.order for s in stages if s.agent_id == SPEC_AGENT_ID), None)
    if spec is None:
        return ""
    stage_map = parse_stage_map(instance, stages)
    # After the spec: a template's plan stage may fan out too (`parallel` in V3),
    # and a parent that only planned before it split is a summary.
    reviewed = any(
        s.stage_type in _REVIEWED and s.order > spec and stage_map.get(s.key) is StageStatus.DONE
        for s in stages
    )
    pending = [
        s.key
        for s in stages
        if s.stage_type == StageType.GATE and stage_map.get(s.key) not in _SETTLED
    ]
    if not (reviewed and pending):
        return ""
    return (
        f"Its children are resolved, but it ran its own stages through review and its "
        f"{', '.join(pending)} has not run. Run it (loregarden_start_orchestration), or "
        "close it saying why its own work needs no gate."
    )


def unlanded_own_work(session: Session, ticket: Ticket) -> str:
    """Why ``ticket`` must not be closed as a summary, or "" when nothing of its own waits.

    "" when a landing is on record, when it has no branch in a repository, or
    when every commit of its own is already held by its target or the base. When
    git cannot tell, the answer is a reason too: closing on "unknown" is how the
    work was lost.
    """
    if ticket.landed_sha:
        return ""
    gate = own_gate_pending(session, ticket)
    if gate:
        return gate
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return ""
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return ""
    branch = resolve_ticket_branch(ticket)
    tip = rev(repo_root, f"refs/heads/{branch}")
    if not tip:
        return ""
    base = base_branch(workspace)
    target = target_branch_name(session, ticket, workspace)
    try:
        if not carries_own_work(repo_root, tip, base):
            return ""
        for ref in dict.fromkeys((target, base)):
            held_by = rev(repo_root, f"refs/heads/{ref}")
            if held_by and landing_commit(repo_root, tip, held_by):
                return ""
    except RuntimeError as exc:
        logger.warning(
            "Could not tell whether %s's own work on %s landed: %s", ticket.external_id, branch, exc
        )
        return (
            f"Could not tell whether its own branch {branch} has landed ({exc}), so it is "
            "not closed as its children's summary."
        )
    return (
        f"Its children are resolved, but its own branch {branch} carries work that is on "
        f"neither {target} nor {base}. Land it (loregarden_land_ticket), or close it saying "
        "where the work went."
    )
