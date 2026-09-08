"""Which review lenses a rework round actually has to re-run.

A parallel review stage fans out to several lenses. When one rejects, the rework
returns to implement and then the WHOLE stage re-runs, every lens, from scratch.
Measured on ticket 181: round one had architecture and static_qa reject while
security passed; round two re-ran all three at about 300k tokens and changed
nothing. It is not rare — several tickets show 12 runs across 3 lenses, four
rounds of re-running everything.

The rule is NOT "skip the lenses that passed". lg-workflow-integrity-499 is
explicit that this would trade tokens for exactly the class of defect the
pipeline exists to catch, and its own example says why: security's round-two run
was not wasted, because the rework had changed a shared reader its earlier
clearance rested on. A lens that passed and whose subject the rework DID touch
still has something to re-derive.

So: re-run a passing lens when the rework diff intersects the files that lens
read. The reads come from lg-workflow-integrity-681.

EVERY UNKNOWN RE-RUNS. No record of what the lens read, no record of what the
rework changed, a run still in flight — each of those means the intersection
cannot be computed, and an uncomputable intersection is not an empty one. With no
read data recorded at all, which is the state the day 681 lands, this decides
exactly what today's code decides and the saving appears only as the data does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from loregarden.models.domain import (
    AgentRun,
    ParallelAgentSpec,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.parallel_stage import latest_member_run, member_passed, member_skill_name
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, col, select


@dataclass(frozen=True)
class LensDecision:
    """One lens, whether it runs this round, and the reason in words.

    The reason is not decoration: AC3 asks for the decision to be recorded on the
    stage rather than reconstructed later, and "security did not run" is only
    reviewable next to why.
    """

    spec: ParallelAgentSpec
    rerun: bool
    reason: str


def _read_paths(run: AgentRun) -> set[str] | None:
    """What this run read, or None if nobody recorded it.

    None is the important return. `[]` with a stamp means the run read nothing in
    the repo; no stamp means the question was never asked, and those must not
    collapse into the same decision (lg-workflow-integrity-675's distinction,
    which is why 681 stores the stamp).
    """
    if run.read_paths_recorded_at is None:
        return None
    return set(json.loads(run.read_paths_json or "[]"))


def _paths_changed_since(session: Session, ticket: Ticket, since: AgentRun) -> set[str] | None:
    """Everything later runs on this ticket changed, or None if that is unknown.

    A single later run with no changed-path record makes the whole diff unknown:
    the files it touched could be exactly the ones a lens depended on, and
    treating "we did not record it" as "it changed nothing" is how a lens gets
    skipped for a change it needed to see.
    """
    later = session.exec(
        select(AgentRun).where(
            col(AgentRun.ticket_id) == ticket.id,
            col(AgentRun.created_at) > since.created_at,
        )
    ).all()
    changed: set[str] = set()
    for run in later:
        if run.changed_paths_recorded_at is None:
            return None
        changed.update(json.loads(run.changed_paths_json or "[]"))
    return changed


def _decide_one(
    session: Session,
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    stage_key: str,
    spec: ParallelAgentSpec,
) -> LensDecision:
    previous = latest_member_run(session, ticket, stage_def, stage_key, spec)
    if previous is None:
        return LensDecision(spec, True, "has not run this stage before")
    if not member_passed(previous):
        # AC4. The lens that rejected always re-runs — it is the one with an
        # open finding, and its own subject is by definition what the rework
        # was aimed at.
        return LensDecision(spec, True, "did not pass its last run")

    read = _read_paths(previous)
    if read is None:
        return LensDecision(spec, True, "no record of what it read last time")
    if not read:
        return LensDecision(
            spec, True, "read nothing in the repo last time, so its basis is unclear"
        )

    changed = _paths_changed_since(session, ticket, previous)
    if changed is None:
        return LensDecision(spec, True, "the rework diff since it passed is not fully recorded")

    overlap = sorted(read & changed)
    if overlap:
        shown = ", ".join(overlap[:3])
        more = f" (+{len(overlap) - 3} more)" if len(overlap) > 3 else ""
        return LensDecision(spec, True, f"the rework changed files it read: {shown}{more}")
    return LensDecision(
        spec, False, f"passed, and the rework touched none of the {len(read)} files it read"
    )


def decide_lenses(
    session: Session, ticket: Ticket, stage_def: WorkflowStageDef, stage_key: str
) -> list[LensDecision]:
    """One decision per member of this parallel stage, in declaration order."""
    return [
        _decide_one(session, ticket, stage_def, stage_key, spec)
        for spec in stage_def.parallel_agents
    ]


def relens_note(stage_def: WorkflowStageDef, decisions: list[LensDecision]) -> str:
    """A one-line-per-lens record of what ran this round and why (AC3)."""
    lines = []
    for decision in decisions:
        skill = member_skill_name(stage_def, decision.spec)
        label = decision.spec.agent_id + (f"/{skill}" if skill else "")
        verb = "re-ran" if decision.rerun else "skipped"
        lines.append(f"{label}: {verb} — {decision.reason}")
    return "Review lenses this round:\n" + "\n".join(lines)


def skipped_members(
    stage_def: WorkflowStageDef, decisions: list[LensDecision]
) -> set[tuple[str, str]]:
    """(agent_id, skill) of every lens this round does not need to re-run.

    Keyed the way `latest_member_run` identifies a member: lanes may share an
    agent and differ only by skill, so agent alone would let one lane's decision
    answer for its siblings.
    """
    return {
        (decision.spec.agent_id, member_skill_name(stage_def, decision.spec))
        for decision in decisions
        if not decision.rerun
    }


def record_relens_decisions(
    session: Session,
    ticket: Ticket,
    stage_def: WorkflowStageDef,
    stage_key: str,
    decisions: list[LensDecision],
) -> None:
    """Write the round's reasoning onto the stage, leaving its status alone.

    On the stage rather than in a log because that is where the workflow pane
    shows it: a lens that quietly did not run is indistinguishable from one that
    ran and found nothing, and the whole risk of this change is that a skip is
    invisible.
    """
    instance = session.exec(
        select(WorkflowInstance).where(col(WorkflowInstance.ticket_id) == ticket.id)
    ).first()
    if instance is None:
        return
    _, stages = resolve_ticket_stages(session, ticket)
    if not stages:
        return
    current = parse_stage_map(instance, stages).get(stage_key)
    if current is None:
        return
    set_stage_status(
        ticket, instance, stages, stage_key, current, note=relens_note(stage_def, decisions)
    )
    session.add_all([ticket, instance])
    session.commit()
