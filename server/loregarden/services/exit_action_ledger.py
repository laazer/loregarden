"""What a workflow gate decided, kept where the next dispatch can read it.

Clearing a gate — a successful recheck, or a person granting authority — does
not complete anything: it lets the responsible stage run again with the newly
executable actions. That continuation is a new run, so the decision has to
outlive the run that raised the gate. It lives on the gate row
(`ExitActionGateLedger` in ``response_json``), keyed by ticket and stage:

- the dispatch overlays recorded grants as GRANTED, and picks up the
  continuation pin (`continuation_for_dispatch`);
- run completion re-gates only what is still outstanding
  (`outstanding_exit_actions`), so an attested action or an approved operator
  judgment is never asked about twice in one continuation chain.

Reads of a single gate are strict and raise: an unreadable gate must never
look like an empty one, because an empty gate would let approve through.
"""

from __future__ import annotations

import json
import logging

from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AuthorityStatus,
    ExitActionGateLedger,
    ExitActionRequirementKind,
    ExitActionResolution,
    HumanRequiredExitAction,
    WorkflowStageDef,
)
from loregarden.services.exit_actions import allowed_resolution_actions, resolve_exit_actions
from pydantic import ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class UnreadableExitActionGateError(ValueError):
    """A workflow gate whose stored exit-action payload cannot be parsed."""


def read_gate_resolution(approval: Approval) -> ExitActionResolution:
    """The gate's unresolved actions, with ``allowed_actions`` recomputed from them.

    The stored ``allowed_actions`` is display data: trusting it would let a
    payload written by any path decide what the server accepts.
    """
    try:
        resolution = ExitActionResolution.model_validate_json(approval.tool_input_json or "")
    except ValidationError as exc:
        raise UnreadableExitActionGateError(
            f"Workflow gate {approval.id} carries an unreadable exit-action payload, so "
            "nothing on it can be approved. Reject it and re-run the stage to raise a "
            "fresh gate."
        ) from exc
    resolution.allowed_actions = allowed_resolution_actions(resolution.human_required_actions)
    return resolution


def read_gate_ledger(approval: Approval) -> ExitActionGateLedger:
    """Strict read of one gate's ledger; raises when it is unreadable."""
    try:
        return ExitActionGateLedger.model_validate_json(approval.response_json or "{}")
    except ValidationError as exc:
        raise UnreadableExitActionGateError(
            f"Workflow gate {approval.id} carries an unreadable exit-action ledger."
        ) from exc


def write_gate_ledger(approval: Approval, ledger: ExitActionGateLedger) -> None:
    approval.response_json = ledger.model_dump_json()


def gate_is_operator_judgment_only(approval: Approval) -> bool:
    """Whether every action on the gate is a decision, which a sign-off may make.

    The unattended design-plan sign-off may answer operator judgment; it must
    never grant authority or stand in for a recheck. An unreadable gate is
    never eligible, and says so.
    """
    try:
        resolution = read_gate_resolution(approval)
    except UnreadableExitActionGateError:
        logger.warning(
            "Gate %s is unreadable; leaving it for a person instead of signing it off",
            approval.id,
            exc_info=True,
        )
        return False
    actions = resolution.human_required_actions
    return bool(actions) and all(
        action.requirement.kind == ExitActionRequirementKind.OPERATOR_JUDGMENT for action in actions
    )


def _stage_ledgers(
    session: Session, ticket_id: str | None, stage_key: str
) -> list[tuple[Approval, ExitActionGateLedger]]:
    """Resolved gates for this ticket and stage, oldest first, with their ledgers.

    An unreadable ledger is skipped with a warning. Skipping fails closed: it
    can only withhold a grant or a settled action, so more is asked of a
    person, never less.
    """
    if not ticket_id or not stage_key:
        return []
    rows = session.exec(
        select(Approval)
        .where(
            Approval.ticket_id == ticket_id,
            Approval.stage_key == stage_key,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
            Approval.status == ApprovalStatus.APPROVED,
        )
        .order_by(col(Approval.created_at).asc())
    ).all()
    ledgers: list[tuple[Approval, ExitActionGateLedger]] = []
    for approval in rows:
        try:
            ledgers.append((approval, read_gate_ledger(approval)))
        except UnreadableExitActionGateError:
            logger.warning(
                "Ignoring gate %s on %s/%s: its ledger is unreadable",
                approval.id,
                ticket_id,
                stage_key,
                exc_info=True,
            )
    return ledgers


def overlay_granted_authority(
    session: Session,
    authority: dict[str, AuthorityStatus],
    *,
    ticket_id: str | None,
    stage_key: str,
) -> dict[str, AuthorityStatus]:
    """Policy statuses with this stage's recorded grants applied.

    Only a GRANTABLE scope becomes GRANTED: a grant is a person saying yes to
    something policy permits, so a scope policy now denies, or that nothing
    observed, stays as it is.
    """
    granted = {
        scope
        for _, ledger in _stage_ledgers(session, ticket_id, stage_key)
        for scope in ledger.granted_authority_scopes
    }
    return {
        scope: (
            AuthorityStatus.GRANTED
            if status == AuthorityStatus.GRANTABLE and scope in granted
            else status
        )
        for scope, status in authority.items()
    }


def continuation_for_dispatch(session: Session, run: AgentRun) -> ExitActionGateLedger | None:
    """The cleared gate this dispatch continues, claiming it on first sight.

    Idempotent per run: a run whose prompt is rendered twice (an external
    harness re-serving a stage) finds the gate it already claimed. A pin is
    claimed once, so a later re-run of the stage (rework, a manual retry) is a
    fresh attempt that redoes every executable action.
    """
    ledgers = _stage_ledgers(session, run.ticket_id, run.stage_key)
    for _, ledger in ledgers:
        if ledger.continuation_run_id == run.id:
            return ledger
    for approval, ledger in reversed(ledgers):
        if ledger.continuation_action_keys and not ledger.continuation_run_id:
            ledger.continuation_run_id = run.id
            write_gate_ledger(approval, ledger)
            session.add(approval)
            return ledger
    return None


def _continued_ledger(session: Session, run: AgentRun) -> ExitActionGateLedger | None:
    for _, ledger in _stage_ledgers(session, run.ticket_id, run.stage_key):
        if ledger.continuation_run_id == run.id:
            return ledger
    return None


def settled_action_keys(session: Session, run: AgentRun) -> list[str]:
    """Actions this run's continuation chain has settled, including its own attestations."""
    ledger = _continued_ledger(session, run)
    carried = (
        [*ledger.settled_action_keys, *ledger.approved_judgment_keys] if ledger is not None else []
    )
    completed = json.loads(run.completed_exit_action_keys_json or "[]")
    return list(dict.fromkeys([*carried, *completed]))


def stored_run_snapshot(run: AgentRun) -> dict | None:
    """The runtime snapshot the run was dispatched with, or None (fail closed)."""
    if not run.runtime_exit_action_snapshot_json:
        return None
    try:
        return json.loads(run.runtime_exit_action_snapshot_json)
    except json.JSONDecodeError:
        # Fail closed: with no snapshot every requirement resolves to unknown,
        # so the gate lists all of them for a person.
        logger.warning(
            "run %s: unreadable runtime exit-action snapshot; treating it as unknown", run.id
        )
        return None


def outstanding_exit_actions(
    session: Session, run: AgentRun | None, stage: WorkflowStageDef
) -> tuple[list[HumanRequiredExitAction], list[str]]:
    """What a person must still resolve after ``run``, and what is already settled.

    With no run (an agentless stage) nothing is settled and nothing was
    observed, so every action resolves against an empty snapshot.
    """
    if run is None:
        return resolve_exit_actions(stage, None).human_required_actions, []
    settled = settled_action_keys(session, run)
    resolution = resolve_exit_actions(stage, stored_run_snapshot(run))
    outstanding = [
        action for action in resolution.human_required_actions if action.action_key not in settled
    ]
    return outstanding, settled
