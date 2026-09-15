"""Say in words what a stage is, so an agent does not have to infer it.

A parallel stage and an agentless human gate both show ``agent_id: ""`` in the
stage view — the members of the first live in ``parallel_agents``, the second
has nobody. On blobert ticket 26 a reader keyed on "no agent → human gate",
raised a gate approval on the three-reviewer ``script_review`` stage, and the
ticket reached ``ac_gate`` with no review run (lg-workflow-integrity-744).
The guard there refuses the wrong move; this names the right one first.

One helper, used by every response that hands an agent a stage to act on —
``get_ticket``, ``complete_stage``, ``begin_external_stage`` — so the wording
cannot drift between them.
"""

from __future__ import annotations

from loregarden.core.workflow_terminal import is_terminal_stage
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkflowStageDef
from loregarden.services.studio_routing import (
    PARALLEL_STAGE_TYPE,
    VERIFY_STAGE_TYPE,
    is_agentless_stage,
    is_parallel_stage,
    ticket_stage_definition,
)
from sqlmodel import Session, select

_RUN_IT = "start_stage (begin_external_stage from an external driver)"
_CLASSIFY_STAGE_TYPE = "classify"  # py-org: allow-string
_GATE_STAGE_TYPE = "gate"  # py-org: allow-string


def _run_tally(session: Session, ticket: Ticket, stage_key: str) -> tuple[int, int]:
    """(succeeded, running) agent runs for this stage on this ticket."""
    statuses = session.exec(
        select(AgentRun.status).where(
            AgentRun.ticket_id == ticket.id, AgentRun.stage_key == stage_key
        )
    ).all()
    succeeded = sum(1 for s in statuses if s == RunStatus.SUCCEEDED)
    running = sum(1 for s in statuses if s == RunStatus.RUNNING)
    return succeeded, running


def _history(succeeded: int, running: int) -> str:
    if running:
        return f"{running} running now"
    if succeeded:
        return f"{succeeded} succeeded run(s)"
    return "none has run"


def describe_stage_shape(session: Session, ticket: Ticket, stage: WorkflowStageDef) -> str:
    """One line: what kind of stage this is, what has run, and the next primitive."""
    if is_terminal_stage(stage):
        return f"{stage.key}: terminal — nothing runs; the workflow finishes here."
    if is_agentless_stage(stage):
        return (
            f"{stage.key}: human gate — no agent; request_approval opens the inbox "
            "item and a person resolves it."
        )
    succeeded, running = _run_tally(session, ticket, stage.key)
    history = _history(succeeded, running)
    if is_parallel_stage(stage):
        members = ", ".join(m.agent_id for m in stage.parallel_agents)
        return (
            f"{stage.key}: {PARALLEL_STAGE_TYPE} — {len(stage.parallel_agents)} members "
            f"({members}); {history}. {_RUN_IT} fans them out; it is not a human gate."
        )
    if stage.stage_type == _CLASSIFY_STAGE_TYPE:
        routes = ", ".join(r.agent_id for r in stage.classify_routes) or stage.agent_id
        return (
            f"{stage.key}: classify — routes to one of ({routes}) by ticket; {history}. "
            f"{_RUN_IT} picks the route."
        )
    if stage.stage_type == _GATE_STAGE_TYPE:
        agent = stage.agent_id or "gatekeeper"
        return f"{stage.key}: gate — {agent} runs the checks; {history}. {_RUN_IT}."
    if stage.stage_type == VERIFY_STAGE_TYPE:
        return f"{stage.key}: verify — {stage.agent_id or 'verifier'} checks; {history}. {_RUN_IT}."
    tail = (
        "complete_stage to advance, or request_approval for a sign-off."
        if succeeded and not running
        else f"{_RUN_IT}."
    )
    return f"{stage.key}: agent {stage.agent_id}; {history}. {tail}"


def current_stage_shape(session: Session, ticket: Ticket) -> str:
    """The shape line for the stage the cursor sits on, or "" off any stage."""
    stage = ticket_stage_definition(session, ticket)
    return describe_stage_shape(session, ticket, stage) if stage else ""
