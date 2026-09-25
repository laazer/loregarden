"""Every block is classified *and* answered for — not classified and abandoned.

749 gave a block a kind; 750 gave it a repair turn. Both worked, and neither was
reached: three call sites out of twenty-five offered the repair, and the
reconciler's sweep backfilled a kind onto everything else, so a block nothing had
done anything about looked exactly like a repaired one. These are the tests for
the seam that closes that (802) — including the enumeration that makes a new
block writer which skips it fail the suite.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    BlockKind,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.block_repair import repair_pinned
from loregarden.services.block_settlement import settle_block, sweep_unclassified_blocks
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select
from tests.history_helpers import decision_kinds

PARALLEL_STAGE = "script_review"


def _clean_exit_with_no_report() -> str:
    """A CLI that exited 0 and printed prose but never the sentinel block.

    The single most common non-work block measured over 30 days (16 of 66), and
    until 802 the only block branch that called neither half of the pair.
    """
    return "I have finished reviewing the changes and everything looks correct."


@pytest.fixture
def review_ticket(db_session: Session):
    """A ticket parked on a real parallel stage, with a live orchestration run.

    Built in a fixture rather than in the tests: a repro whose *setup* breaks
    must fail as an ERROR, not as the behaviour under test.
    """
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert template and workspace
    stages = get_template_stages(template)
    stage_def = next(s for s in stages if s.key == PARALLEL_STAGE)
    assert stage_def.stage_type == "parallel", "the fixture's premise: this stage fans out"

    ticket = Ticket(
        external_id="settlement-review",
        workspace_id=workspace.id,
        title="Settlement review",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=PARALLEL_STAGE,
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="gdscript_reviewer",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=PARALLEL_STAGE,
            stages_json=initial_stages_json(stages),
        )
    )
    orch_run = OrchestrationRun(
        run_code="orch_settlement",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.RUNNING,
        current_stage_key=PARALLEL_STAGE,
    )
    db_session.add(orch_run)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.refresh(orch_run)
    assert orch_run.auto_repair, "the fixture's premise: this run may spend a repair turn"
    return ticket, orch_run, stage_def


def test_a_parallel_member_that_succeeded_with_no_report_takes_a_repair_turn(
    db_session: Session, monkeypatch, review_ticket
):
    """AC5. A fan-out is where a stage report is likeliest to be lost — N lenses,
    N chances to drop the envelope — and it was the one shape a repair could
    never reach, because `repair_pin_applies` excluded parallel stages.
    `lg-initiatives-cross-755` is the live instance: three planners SUCCEEDED,
    one never printed the envelope, and the operator requeued by hand.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, stage_def = review_ticket

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED  # exit 0 either way
        run.stderr = ""
        if run.agent_id == "static_qa":
            run.stdout = _clean_exit_with_no_report()
        else:
            run.stdout = (
                "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
                '{"status": "pass", "confidence": 0.95}\n<<<END_STAGE_REPORT>>>\n'
            )
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    BuiltinOrchestrator(db_session)._execute_parallel_stage(
        ticket, orch_run, stage_def, PARALLEL_STAGE
    )
    db_session.refresh(ticket)

    decisions = decision_kinds(db_session, ticket.id)
    assert OrchestratorDecision.DISPATCHED_REPAIR.value in decisions
    assert repair_pinned(ticket, PARALLEL_STAGE)
    # Not a human-waiting block: the ticket is going round again, not parked.
    assert ticket.state is not TicketState.BLOCKED


def test_a_clean_exit_with_no_stage_report_is_settled_without_advancing_the_stage(
    db_session: Session, review_ticket
):
    """AC2. The repair turn does not weaken the fail-closed rule: the stage is
    re-armed to run *again* and must emit its own report. What must never happen
    is the stage reaching DONE on a report nobody produced.
    """
    from loregarden.services.orchestration import OrchestrationService
    from loregarden.services.run_completion import advance_stage_after_run

    ticket, orch_run, _ = review_ticket
    orch = OrchestrationService(db_session)
    run = AgentRun(
        run_code="run_noreport",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        stage_key=PARALLEL_STAGE,
        status=RunStatus.SUCCEEDED,
        orchestration_run_id=orch_run.id,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    advance_stage_after_run(
        orch, ticket, run, None, RunStatus.SUCCEEDED, "", stdout=_clean_exit_with_no_report()
    )
    db_session.commit()
    db_session.refresh(ticket)

    decisions = decision_kinds(db_session, ticket.id)
    assert OrchestratorDecision.CLASSIFIED_BLOCK.value in decisions
    assert OrchestratorDecision.DISPATCHED_REPAIR.value in decisions
    assert ticket.block_kind is BlockKind.HARNESS
    assert repair_pinned(ticket, PARALLEL_STAGE)
    # Fail-closed, still: whatever happens next, this stage did not pass. The
    # repair turn re-runs it and must emit its own report like anyone else.
    assert ticket.workflow_stage_status is not StageStatus.DONE
    assert ticket.workflow_stage_key == PARALLEL_STAGE


def test_the_sweep_says_why_a_block_it_classified_got_no_repair(db_session: Session, review_ticket):
    """AC4. The sweep runs on the reconciler, over tickets whose runs are long
    over, so it can never spend a repair turn. Before 802 it stamped a kind and
    stopped, which is what made "every block has a kind" true while nothing had
    been done about any of them.
    """
    ticket, _, _ = review_ticket
    ticket.blocking_issues = "Orchestration lease expired for run orch_dead"
    ticket.block_kind = None
    ticket.workflow_stage_status = StageStatus.BLOCKED
    db_session.add(ticket)
    db_session.commit()

    assert sweep_unclassified_blocks(db_session) == 1
    db_session.refresh(ticket)

    decisions = decision_kinds(db_session, ticket.id)
    assert ticket.block_kind is BlockKind.HARNESS
    assert OrchestratorDecision.REPAIR_ESCALATED.value in decisions
    assert OrchestratorDecision.DISPATCHED_REPAIR.value not in decisions
    assert sweep_unclassified_blocks(db_session) == 0  # idempotent


def test_a_settled_block_that_gets_no_repair_records_a_reason_worth_reading(
    db_session: Session, review_ticket
):
    """The gate on an escalation is only that a reason exists; this asserts it
    names the stage, so the history line is readable on its own."""
    ticket, _, _ = review_ticket
    settlement = settle_block(
        db_session,
        ticket,
        stage_key="implement",
        message="something went wrong",
        declared=BlockKind.WORK,
    )

    assert settlement.repair_armed is False
    assert settlement.kind is BlockKind.WORK
    assert "implement" in settlement.reason


# --------------------------------------------------------------------------- #
# AC6 — the enumeration                                                        #
# --------------------------------------------------------------------------- #

#: Every function in the tree that leaves a ticket or its stage BLOCKED, and how
#: it answers for the block. `settles` calls `settle_block` itself; `delegates`
#: reaches a settler through the named function; `exempt` says, in words, why
#: this block needs neither.
#:
#: The list is the point. A new block writer is not in it, so the scan below
#: fails and its author has to state which of the three this is — which is the
#: only reason 749/750 ever covered three sites out of twenty-five.
BLOCK_WRITERS: dict[str, str] = {
    "agents/executors/permission_bridge.py:_mark_stage_blocked": (
        "delegates: run_completion.advance_stage_after_run — the bridge blocks the stage mid-"
        "dispatch (a denied permission, a scope denial) and the run it is inside then finishes "
        "through complete_run, which settles the block with the run's own message"
    ),
    "api/orchestration.py:callback_block": "delegates: orchestration_callbacks.block_ticket",
    "mcp/tools.py:execute_tool": "delegates: orchestration_callbacks.block_ticket",
    "services/approval_resolution.py:apply_park_resolution": (
        "exempt: a person rejected the park, so the block is their own decision, taken in the "
        "inbox item that is still in front of them — there is nothing to classify for them and "
        "no agent turn that could overrule it"
    ),
    "services/approval_resolution.py:apply_gate_resolution": (
        "exempt: same — a person rejected the gate and no rework route existed, so the block is "
        "the decision they just made rather than a fault anything can repair"
    ),
    "services/artifact_service.py:block_ticket_for_unresolved_blocker": "settles",
    "services/builtin_orchestrator.py:execute": ("delegates: orchestration_callbacks.block_ticket"),
    "services/builtin_orchestrator.py:_run_parallel_stage_or_stop": (
        "delegates: orchestration_callbacks.block_ticket"
    ),
    "services/builtin_orchestrator.py:_run_sequential_stage": (
        "delegates: orchestration_callbacks.block_ticket"
    ),
    "services/landing.py:_block": "delegates: artifact_service.block_ticket_for_unresolved_blocker",
    "services/orchestration_callbacks.py:block_ticket": "settles",
    "services/orchestration_callbacks.py:pause_for_rework_decision": (
        "delegates: orchestration_callbacks.block_ticket"
    ),
    "services/parallel_stage.py:prepare_tree_for_parallel_stage": "settles",
    "services/parallel_stage.py:_route_parallel_stage_failures": "settles",
    "services/run_completion.py:_reroute_or_block_for_rework": "settles",
    "services/run_completion.py:settle_stage_after_failed_completion": "settles",
    "services/run_completion.py:_block_for_usage_limit": "settles",
    "services/run_completion.py:_advance_clean_exit": "settles",
    "services/run_completion.py:_rearmed_for_transient_retry": "settles",
    "services/run_completion.py:advance_stage_after_run": "settles",
    "services/run_service.py:settle_stranded_stages": "settles",
    "services/stage_retry_budget.py:enforce_stage_retry_budget": (
        "delegates: orchestration_callbacks.block_ticket"
    ),
}

_SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "loregarden"

#: Calls that leave a ticket or its stage blocked. `block_ticket` is the funnel;
#: the other three are the raw writes that reach BLOCKED without it.
_STAGE_WRITERS = frozenset({"set_stage_status", "finalize_stage", "_set_stage_status"})


def _writes_a_block(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        called = (
            sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", "")
        )
        if called == "block_ticket":
            return True
        arguments = list(sub.args) + [kw.value for kw in sub.keywords]
        blocked = any(isinstance(a, ast.Attribute) and a.attr == "BLOCKED" for a in arguments)
        if blocked and called in _STAGE_WRITERS | {"choose"}:
            return True
    return False


def _called_names(node: ast.AST) -> set[str]:
    return {
        sub.func.attr if isinstance(sub.func, ast.Attribute) else getattr(sub.func, "id", "")
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
    }


def _settling_names(tree: ast.Module) -> set[str]:
    """`settle_block`, plus this module's own wrappers around it.

    `run_completion` reaches the helper through a private `_settle_block` that
    supplies the run's parent orchestration and transitions, which is the right
    shape — the scan follows it rather than demanding every branch repeat that
    plumbing. Resolved to a fixed point so a wrapper around a wrapper counts.
    """
    settling = {"settle_block"}
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    changed = True
    while changed:
        changed = False
        for name, node in functions.items():
            if name not in settling and _called_names(node) & settling:
                settling.add(name)
                changed = True
    return settling


def _discovered_block_writers() -> dict[str, bool]:
    """Every function that blocks, mapped to whether it settles the block itself."""
    found: dict[str, bool] = {}
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        settling = _settling_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _writes_a_block(node):
                key = f"{path.relative_to(_SOURCE_ROOT).as_posix()}:{node.name}"
                found[key] = bool(_called_names(node) & settling)
    return found


def test_every_block_writer_is_enumerated_and_answers_for_its_block():
    """AC6. A new path that leaves a ticket blocked must say which it is."""
    discovered = _discovered_block_writers()

    unenumerated = sorted(set(discovered) - set(BLOCK_WRITERS))
    assert not unenumerated, (
        "These leave a ticket blocked and are not in BLOCK_WRITERS. Route the block through "
        "`block_settlement.settle_block` (or through a function that does) and record which "
        "here — a block with no recorded disposition reads as handled when nothing handled "
        f"it: {unenumerated}"
    )

    gone = sorted(set(BLOCK_WRITERS) - set(discovered))
    assert not gone, f"BLOCK_WRITERS names functions that no longer block: {gone}"


def test_every_writer_claiming_to_settle_actually_calls_the_helper():
    """The inventory is a claim; this is the part of it a scan can check."""
    discovered = _discovered_block_writers()
    liars = sorted(
        key
        for key, answer in BLOCK_WRITERS.items()
        if answer == "settles" and not discovered.get(key, False)
    )
    assert not liars, f"Enumerated as 'settles' but never calls settle_block: {liars}"


def test_every_exemption_gives_a_reason():
    """The gate can only check that a reason exists; a reviewer checks it is true."""
    thin = sorted(
        key
        for key, answer in BLOCK_WRITERS.items()
        if answer.startswith("exempt") and len(answer) < len("exempt: ") + 40
    )
    assert not thin, f"These exemptions say nothing a reader can check: {thin}"


#: The two halves of settling a block. `settle_block` is their only caller, and
#: this is what enforces that: Python has no way to say "one importer", and the
#: organization gate rejects a cross-module private import, so the rule is a
#: test rather than an underscore.
_HALVES = frozenset({"record_block", "offer_repair"})


def test_the_classify_and_repair_halves_are_not_separately_callable():
    """AC1's enforcement. A new block writer cannot reach `record_block` without
    `offer_repair` coming with it, which is exactly how 749/750 came to cover
    three sites out of twenty-five: the pair was opt-in per call site."""
    offenders: list[str] = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(_SOURCE_ROOT).as_posix()
        if relative == "services/block_settlement.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        owns = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                offenders += [
                    f"{relative} imports {alias.name}"
                    for alias in node.names
                    if alias.name in _HALVES
                ]
            # A call to a name this module defines itself is its own function,
            # not the half — `block_repair.offer_repair` is not `block_repair`
            # calling the settlement's half.
            if isinstance(node, ast.Call):
                called = getattr(node.func, "id", "")
                if called in _HALVES and called not in owns:
                    offenders.append(f"{relative} calls {called}")
    assert not offenders, (
        "Only `block_settlement` may reach the halves; everything else calls `settle_block`, "
        f"so that classifying and offering the repair cannot come apart: {sorted(set(offenders))}"
    )
