"""The checks a stage passes before its agent starts, each able to park it.

In order: the environment preflight (`services.doctor`), the handoff boundary
(`services.handoff_boundary`), then sibling file overlap
(`services.sibling_overlap_park`). Each failure parks the stage on an inbox
approval rather than blocking the ticket (see `services.stage_parking`).

Every dispatch path converges on `CliAgentExecutor.execute`, which calls this
once the execution root and branch are settled. Lifted out of the executor when
the overlap check pushed it past its size cap.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import AgentRun, DoctorStatus, RunStatus, Ticket, Workspace
from loregarden.services.doctor import park_for_environment, preflight_run, preflight_summary
from loregarden.services.git_boundary import boundary_of_tree, stamp_run_boundary
from loregarden.services.handoff_boundary import (
    boundary_enforced,
    park_for_boundary,
    verdict_proceeds,
    verify_run_boundary,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.path_claims import SiblingOverlap
from loregarden.services.preflight_ledger import PreflightLedger
from loregarden.services.sibling_overlap_park import (
    overlaps_to_park_on,
    park_for_sibling_overlap,
)
from loregarden.services.worktree_snapshot import TreeSnapshot
from sqlmodel import Session

logger = logging.getLogger(__name__)


def park_unless_fit_to_run(
    session: Session,
    orchestration: OrchestrationService,
    run: AgentRun,
    ticket: Ticket,
    workspace: Workspace,
    *,
    repo_root: Path,
    before: TreeSnapshot,
    ledger: PreflightLedger,
) -> AgentRun | None:
    """Record the tree this run inherited and run the checks. Returns the parked
    (cancelled) run when the stage must not proceed, or None to carry on.

    A run carrying `dispatch_waiver_approval_id` was dispatched by a person who
    saw a check fail and said run it anyway. It skips the *parking*, never the
    checking: the preflight and boundary results are still recorded on the run,
    because "a human waived it" and "the environment was fine" must not read
    the same afterwards. `maybe_warn_dispatch_waiver` puts it in the log.
    """
    stamp_run_boundary(session, run, boundary_of_tree(before))
    waived = bool(run.dispatch_waiver_approval_id)

    # The environment first: a stage started in a checkout with core.bare set,
    # or under a leaked GIT_DIR, fails in ways that point at anything but the
    # cause. Recorded on the run whatever happens next.
    preflight = preflight_run(session, run, workspace, repo_root, ledger=ledger)
    if any(finding.status is DoctorStatus.FAIL for finding in preflight) and not waived:
        park_for_environment(session, run=run, ticket=ticket, summary=preflight_summary(preflight))
        return _cancel(orchestration, run, "Environment preflight failed")

    verdict = verify_run_boundary(session, run, ticket)
    if not (verdict_proceeds(verdict) or not boundary_enforced(workspace) or waived):
        park_for_boundary(session, run=run, ticket=ticket, verdict=verdict)
        return _cancel(orchestration, run, f"Boundary check {verdict.value}")

    overlaps = [] if waived else _overlaps(session, ticket)
    if overlaps:
        park_for_sibling_overlap(session, run=run, ticket=ticket, overlaps=overlaps)
        return _cancel(orchestration, run, "Sibling tickets claim the same files")
    return None


def _overlaps(session: Session, ticket: Ticket) -> list[SiblingOverlap]:
    """The siblings to ask about, or [] when the check itself cannot answer.

    The check is advice about ordering; the stage it guards can run correctly
    without it, and the prompt still carries its own warning. A failure is
    logged with its traceback rather than stopping the run.
    """
    try:
        return overlaps_to_park_on(session, ticket)
    except Exception:  # noqa: BLE001 - silent-ok: logged with traceback; the stage runs unordered, as it did before this check existed
        logger.exception("sibling overlap check failed for %s", ticket.external_id)
        return []


def _cancel(orchestration: OrchestrationService, run: AgentRun, why: str) -> AgentRun:
    return orchestration.complete_run(
        run,
        status=RunStatus.CANCELLED,
        stderr=f"{why}: awaiting human confirmation.",
        advance_workflow=False,
    )
