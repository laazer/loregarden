"""Resolve the `run_id` a run-scoped MCP tool was given, in any form an agent holds.

A stage prompt names its run as `# Run: run_xxx` (the agent run's code) and, below,
as `orchestration_run_id`. Agents pass whichever they read first — the code, the
agent run's UUID, or the orchestration UUID — and the tools used to accept only the
last, refusing the others with "Orchestration run not found". All four forms name
one ticket; only the stage-lifecycle tools also need an orchestration behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from sqlmodel import Session, select

from loregarden.models.domain import AgentRun, OrchestrationRun

RunT = TypeVar("RunT", AgentRun, OrchestrationRun)

_ACCEPTED_FORMS = (
    "the orchestration_run_id UUID, the agent run's UUID, "
    "or either run code (e.g. run_1a2b3c from the prompt's `# Run:` line)"
)

#: `run_id` descriptions, worded from what the prompt shows the agent.
TICKET_RUN_ID_DESCRIPTION = (
    "This run: the `agent_run` code from the prompt (run_…), the agent run's UUID, "
    "or `orchestration_run_id`. A standalone stage run is accepted."
)
ORCHESTRATION_RUN_ID_DESCRIPTION = (
    "The orchestration run: `orchestration_run_id`, or the `agent_run` code (run_…) "
    "or UUID of a stage run inside it."
)


@dataclass(frozen=True)
class RunRef:
    """What a `run_id` argument resolved to."""

    named: str
    ticket_id: str
    orchestration: OrchestrationRun | None

    def require_orchestration(self, tool: str) -> OrchestrationRun:
        if self.orchestration is None:
            raise ValueError(
                f"{tool} needs an orchestration run, and '{self.named}' is a standalone "
                "stage run with no orchestration run behind it. Ticket-scoped tools "
                "(attach_artifact, attach_evidence, request_approval) accept it."
            )
        return self.orchestration


def resolve_run_ref(session: Session, run_ref: str) -> RunRef:
    orchestration = session.get(OrchestrationRun, run_ref) or _one_by_code(
        session, OrchestrationRun, run_ref
    )
    if orchestration is not None:
        return RunRef(named=run_ref, ticket_id=orchestration.ticket_id, orchestration=orchestration)

    agent_run = session.get(AgentRun, run_ref) or _one_by_code(session, AgentRun, run_ref)
    if agent_run is None:
        raise ValueError(f"Run not found: '{run_ref}'. run_id accepts {_ACCEPTED_FORMS}.")
    if not agent_run.ticket_id:
        raise ValueError(
            f"Agent run '{run_ref}' belongs to no ticket, so it has nothing to act on."
        )
    parent = (
        session.get(OrchestrationRun, agent_run.orchestration_run_id)
        if agent_run.orchestration_run_id
        else None
    )
    return RunRef(named=run_ref, ticket_id=agent_run.ticket_id, orchestration=parent)


def _one_by_code(session: Session, model: type[RunT], run_code: str) -> RunT | None:
    # Codes are six random hex digits, so two runs can share one. Two matches is
    # a question only the caller can answer; picking one would attach to the
    # wrong ticket without a word.
    matches = session.exec(select(model).where(model.run_code == run_code).limit(2)).all()
    if len(matches) > 1:
        raise ValueError(
            f"Run code '{run_code}' is ambiguous: more than one run carries it. "
            "Pass the orchestration_run_id UUID instead."
        )
    return matches[0] if matches else None
