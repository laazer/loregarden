"""What a stage can be told before its agent is spawned, at zero tokens.

A review, verify or gate stage spawned an agent every time, even when a check
costing nothing already knew the answer. Two of those answers are taken here:

- **Missing inputs block.** A stage names what it must be given
  (`WorkflowStageDef.required_inputs`). Each is read through the source the
  agent would read: the diff with git in the ticket's worktree, the criteria
  from the ticket row. One that is missing blocks the stage without spawning,
  naming the input and what to do about it. Both drivers ask before they choose
  a stage (`readiness_gate.block_if_unready`), and `start_run` asks again at
  the paid call, which covers a stage started by hand.
- **An unchanged tree carries a pass forward.** A review lens that passed at a
  commit, on a tree nothing has changed since, has nothing new to read
  (`unchanged_since`, used by `review_relens`).

Unknown never blocks and never carries: a ticket with no worktree, a parent
branch git cannot name, or a git call that fails leaves the stage to run as it
always did, with a warning. Only a determinate answer acts.

Both outcomes are recorded as orchestrator decisions with the stage and the
tokens they spared (`STAGE_INPUTS_MISSING`, `CARRIED_FORWARD_PASS`), and
`readiness_counts` sums them per stage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    OrchestratorDecision,
    RunStatus,
    StageInput,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.git_subprocess import run_git
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.parent_ref import live_diff_base
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.ticket_worktree import resolve_ticket_checkout
from sqlmodel import Session, col, func, select

logger = logging.getLogger(__name__)

#: Runs averaged to estimate what a blocked stage would have spent.
_ESTIMATE_RUNS = 20


@dataclass(frozen=True)
class MissingInput:
    """One declared input the stage was not given, why, and what to do next."""

    input: StageInput
    why: str
    remedy: str

    def line(self) -> str:
        return f"{self.input.value}: {self.why}. {self.remedy}"


class StageInputsMissing(ValueError):
    """A dispatch refused because the stage's declared inputs are missing.

    A `ValueError` so every path that turns a refusal into a 409 or a queue
    entry's failure reason does so here too, as for `DependencyHold`.
    """

    def __init__(self, ticket: Ticket, stage_key: str, missing: list[MissingInput]) -> None:
        super().__init__(refusal_message(ticket, stage_key, missing))
        self.missing = missing


def refusal_message(ticket: Ticket, stage_key: str, missing: list[MissingInput]) -> str:
    listed = "; ".join(entry.line() for entry in missing)
    return f"'{stage_key}' on {ticket.external_id} was not started: missing {listed}"


def block_line(stage_key: str, missing: list[MissingInput]) -> str:
    """The short form for `ticket.blocking_issues`, which holds 200 characters inline.

    The full message, with each remedy, is the decision's reason on the history rail.
    """
    names = ", ".join(entry.input.value for entry in missing)
    return f"'{stage_key}' was not started: missing {names}. The history says what to do."


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    result = run_git(args, cwd=cwd, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout


def _diff_missing(session: Session, ticket: Ticket, stage_key: str) -> MissingInput | None:
    """The branch has no change against the commit it was cut from, or None.

    None also when that cannot be told: no worktree with a parent, a parent git
    cannot name, or git failing. Those warn and let the stage run.
    """
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return None
    checkout = resolve_ticket_checkout(session, ticket, workspace)
    if not checkout.parent_branch:
        return None
    base = live_diff_base(checkout.path, checkout.parent_branch)
    if base is None:
        return None
    code, _ = _git(["diff", "--quiet", base], checkout.path)
    if code == 1:
        return None
    if code != 0:
        logger.warning(
            "Readiness: git diff against %s failed in %s (exit %s); %s runs unchecked",
            base,
            checkout.path,
            code,
            stage_key,
        )
        return None
    code, untracked = _git(["ls-files", "--others", "--exclude-standard"], checkout.path)
    if code != 0:
        logger.warning(
            "Readiness: listing untracked files failed in %s (exit %s); %s runs unchecked",
            checkout.path,
            code,
            stage_key,
        )
        return None
    if untracked.strip():
        return None
    return MissingInput(
        StageInput.DIFF,
        f"the branch has no change against {checkout.parent_branch} (merge-base {base[:9]})",
        "Re-run the stage that writes the code, or close the ticket if nothing needed changing.",
    )


def _criteria_missing(ticket: Ticket) -> MissingInput | None:
    if load_criteria(ticket.acceptance_criteria_json):
        return None
    return MissingInput(
        StageInput.ACCEPTANCE_CRITERIA,
        "the ticket states no acceptance criterion",
        "Write them with loregarden_update_ticket, or re-run the spec stage.",
    )


def missing_inputs(
    session: Session, ticket: Ticket, stage_def: WorkflowStageDef
) -> list[MissingInput]:
    """Every input ``stage_def`` declares that the ticket does not have, in declared order."""
    found: list[MissingInput] = []
    for declared in stage_def.required_inputs:
        entry = (
            _diff_missing(session, ticket, stage_def.key)
            if declared is StageInput.DIFF
            else _criteria_missing(ticket)
        )
        if entry is not None:
            found.append(entry)
    return found


def _run_tokens(run: AgentRun) -> int | None:
    parts = [run.input_tokens, run.output_tokens, run.cache_read_tokens, run.cache_write_tokens]
    known = [part for part in parts if part is not None]
    return sum(known) if known else None


def _stage_token_estimate(session: Session, stage_key: str) -> tuple[int | None, int]:
    """Mean tokens of the last succeeded runs at ``stage_key``, and how many were averaged."""
    runs = session.exec(
        select(AgentRun)
        .where(col(AgentRun.stage_key) == stage_key, col(AgentRun.status) == RunStatus.SUCCEEDED)
        .order_by(col(AgentRun.created_at).desc())
        .limit(_ESTIMATE_RUNS)
    ).all()
    measured = [tokens for tokens in map(_run_tokens, runs) if tokens is not None]
    if not measured:
        return None, 0
    return sum(measured) // len(measured), len(measured)


def record_inputs_missing(
    session: Session, ticket: Ticket, stage_key: str, missing: list[MissingInput]
) -> None:
    estimate, averaged = _stage_token_estimate(session, stage_key)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.STAGE_INPUTS_MISSING,
        stage_key=stage_key,
        reason=refusal_message(ticket, stage_key, missing),
        evidence={
            "missing_inputs": [entry.input.value for entry in missing],
            "tokens_saved": estimate,
            "tokens_basis": f"mean of the last {averaged} succeeded runs at {stage_key}",
        },
    )
    session.commit()


def refuse_unready_dispatch(session: Session, ticket: Ticket, stage_def: WorkflowStageDef) -> None:
    """Raise `StageInputsMissing` when ``stage_def`` lacks a declared input.

    Recorded and committed before the raise, so a caller's rollback on the way
    out cannot take the record with it.
    """
    missing = missing_inputs(session, ticket, stage_def)
    if not missing:
        return
    record_inputs_missing(session, ticket, stage_def.key, missing)
    raise StageInputsMissing(ticket, stage_def.key, missing)


def unchanged_since(session: Session, ticket: Ticket, run: AgentRun) -> str | None:
    """``"<from>..<to>"`` when the ticket's tree is what ``run`` started on, else None.

    The run must have started on a clean tree, the tree must be clean now, and
    git must find no difference between the two commits. Anything it cannot
    tell is None.
    """
    since = run.start_head_sha
    if not since or json.loads(run.start_dirty_paths_json or "[]"):
        return None
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return None
    root = resolve_ticket_checkout(session, ticket, workspace).path
    code, head = _git(["rev-parse", "HEAD"], root)
    head = head.strip()
    if code != 0 or not head:
        return None
    code, dirty = _git(["status", "--porcelain"], root)
    if code != 0 or dirty.strip():
        return None
    if head != since:
        code, _ = _git(["diff", "--quiet", since, head], root)
        if code != 0:
            return None
    return f"{since[:9]}..{head[:9]}"


def record_carried_forward(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    *,
    lens: str,
    prior: AgentRun,
    commit_range: str,
    reason: str,
) -> None:
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.CARRIED_FORWARD_PASS,
        stage_key=stage_key,
        reason=f"{lens} carried forward its pass from {prior.run_code}: {reason}.",
        run_id=prior.id,
        evidence={
            "lens": lens,
            "run_code": prior.run_code,
            "commit_range": commit_range,
            "tokens_saved": _run_tokens(prior),
            "tokens_basis": f"what {prior.run_code} spent",
        },
    )


@dataclass(frozen=True)
class StageReadinessCount:
    """Per stage: stages blocked on a missing input, lenses carried forward, tokens spared.

    ``tokens_saved`` sums only the records that had a figure; ``unmeasured``
    counts the ones that did not, so the sum reads as a floor.
    """

    stage_key: str
    inputs_missing: int = 0
    carried_forward: int = 0
    tokens_saved: int = 0
    unmeasured: int = 0


_COUNTED = {
    OrchestratorDecision.STAGE_INPUTS_MISSING.value: "inputs_missing",
    OrchestratorDecision.CARRIED_FORWARD_PASS.value: "carried_forward",
}


def readiness_counts(
    session: Session, *, since: datetime | None = None
) -> list[StageReadinessCount]:
    """Every stage's readiness blocks and carried-forward passes, by stage key."""
    query = (
        select(DomainEvent.payload_json)
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
        .where(func.json_extract(DomainEvent.payload_json, "$.decision").in_(list(_COUNTED)))
    )
    if since is not None:
        query = query.where(col(DomainEvent.created_at) >= since)
    tallies: dict[str, dict[str, int]] = {}
    for raw in session.exec(query).all():
        payload = json.loads(raw)
        tally = tallies.setdefault(
            payload["stage_key"],
            {"inputs_missing": 0, "carried_forward": 0, "tokens_saved": 0, "unmeasured": 0},
        )
        tally[_COUNTED[payload["decision"]]] += 1
        saved = payload.get("tokens_saved")
        if saved is None:
            tally["unmeasured"] += 1
        else:
            tally["tokens_saved"] += saved
    return [StageReadinessCount(stage_key=key, **tally) for key, tally in sorted(tallies.items())]
