"""Agent runs left in flight under an orchestration run that has ended.

Moved out of `OrchestrationCallbackService`, which finishes the orchestration
run and calls this; the rule for what happens to its children stands alone.
"""

from __future__ import annotations

import logging

from loregarden.models.domain import AgentRun, OrchestrationRun, ProcessState, RunStatus
from loregarden.services.capacity_errors import CAPACITY_UNAVAILABLE_PREFIX
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.process_identity import liveness
from loregarden.services.run_interruption import ORPHAN_OF_TERMINAL_ORCH_MESSAGE
from loregarden.services.run_lease import SUPERVISED
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def fail_in_flight_children(
    session: Session, orch: OrchestrationService, orch_run: OrchestrationRun
) -> None:
    """Fail leftover agent runs so they stop reading as live work.

    A run left in flight because the orchestration ended on a capacity wait
    never started: it is the run that waited. It is failed with that
    message, not the generic orphan one, so the run's own record names the
    cause. Recorded as an orphan, lg-milestone-that-1425's spec runs read as
    "parent orchestration is already terminal" and sent the triage agent
    looking for a dead driver instead of a full machine.
    """
    stderr = (
        orch_run.error_message
        if orch_run.error_message.startswith(CAPACITY_UNAVAILABLE_PREFIX)
        else ORPHAN_OF_TERMINAL_ORCH_MESSAGE
    )
    children = session.exec(
        select(AgentRun)
        .where(AgentRun.orchestration_run_id == orch_run.id)
        .where(col(AgentRun.status).in_(list(SUPERVISED)))
    ).all()
    for child in children:
        state = liveness(child.agent_pid, child.agent_pid_identity)
        if state is not ProcessState.GONE:
            # A leftover is a row nobody is working under. A verified-live
            # process is somebody working (757): its row stays, and its own
            # completion — under a parent it will find terminal — says what
            # came of it. The startup reaper skips its parent for the same
            # reason; this is the same rule at the other end of the edge.
            # A pid `ps` could not answer about is spared the same way (862);
            # the lease reaper settles it once it is knowably gone.
            logger.warning(
                "Not failing agent run %s under terminal orchestration %s: pid %s is %s",
                child.run_code,
                orch_run.run_code,
                child.agent_pid,
                "still its process" if state is ProcessState.ALIVE else "of unknown state",
            )
            continue
        orch.complete_run(
            child,
            status=RunStatus.FAILED,
            stderr=stderr,
            advance_workflow=False,
        )
