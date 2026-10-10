"""Park a run the shared checkout was refused to, instead of failing it.

A dirty primary checkout is a fact about this machine, not about the ticket —
the same reasoning that parks a failed environment preflight
(`doctor.park_for_environment`). So the run is cancelled before any agent CLI
starts, the refusal is filed where the operator looks (an ERROR artifact naming
the paths, and an approval in the inbox), and approving re-runs the stage once
the checkout is clean (lg-workflow-integrity-864).

Its own module because `CliAgentExecutor` sits near its size cap.
"""

from __future__ import annotations

from loregarden.models.domain import AgentRun, ArtifactKind, RunStatus, Ticket
from loregarden.services.doctor import park_for_environment
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError
from sqlmodel import Session


def park_on_dirty_checkout(
    session: Session,
    orchestration: OrchestrationService,
    *,
    run: AgentRun,
    ticket: Ticket,
    error: DirtyPrimaryCheckoutError,
) -> AgentRun:
    """File the refusal, park the stage for a human, and cancel the run unstarted."""
    OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind=ArtifactKind.ERROR,
        title=f"Shared checkout refused — {run.stage_key or 'stage'}",
        content=error.artifact_content(stage_key=run.stage_key or ""),
    )
    park_for_environment(session, run=run, ticket=ticket, summary=str(error))
    return orchestration.complete_run(
        run,
        status=RunStatus.CANCELLED,
        stderr=str(error),
        advance_workflow=False,
    )
