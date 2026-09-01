"""The stage retry budget on the *standalone* dispatch paths.

Ticket 105 built the persisted per-(ticket, stage) dispatch counter and wired it
into exactly one place: the loop inside `BuiltinOrchestrator.execute()`. The
manual "Run stage" path (`RunService.start_stage_execution` ->
`start_run_async` -> `OrchestrationService.start_run`) and MCP
`loregarden_start_stage` (`OrchestrationCallbackService.start_stage`) never
reach it, so a whole dispatch path runs with no circuit breaker at all.

These tests pin the contract the standalone paths must honour. Three of them
exist specifically to stop a naive fix:

TRAP 1 — `_prepare_stage_start` (the seam `start_run` uses) fires ONCE PER
PARALLEL MEMBER: `builtin_orchestrator._start_parallel_stage_runs` loops
`start_run` once per spec, as do `external_harness` and `stage_fanout_service`.
Recording a dispatch there naively counts a 3-member parallel stage as 3
attempts and burns a 5-attempt budget in two passes — a direct violation of
ticket 105 AC1.3. `test_a_three_member_parallel_dispatch_counts_as_one_attempt`
is that regression.

TRAP 2 — `_prepare_stage_start` already calls `refresh_stage_retry_budget` when
the ticket or the stage is BLOCKED (orchestration.py), which WIPES the counter.
Implementing the standalone refusal as a ticket block therefore makes the
breaker self-clearing on the very next start.
`test_the_refusal_leaves_the_ticket_unblocked_and_the_counter_intact` is that
regression: the refusal must be a raised error that leaves ticket state alone.

TRAP 3 — MCP `loregarden_start_stage` reaches neither seam. It calls
`OrchestrationCallbackService.start_stage`, which only flips stage status and
publishes an event; it never calls `start_run` and creates no AgentRun. It needs
its own check site, and its `force` override arrives as a tool argument (so the
`_normalize_stage_scoped` normalizer has to carry it), not an HTTP body field.

The contract these tests assume, since it does not exist yet:

- The refusal is a `ValueError` (the idiom `start_run` already uses for every
  other refusal, and what the API layer maps to a non-2xx). A subclass is fine.
  Its message names the numeric budget and the word "force", per AC2 — it has
  to tell a human both what tripped and how to override it.
- The override is `force: bool = False`, threaded through
  `RunService.start_stage_execution` / `start_run_async` /
  `OrchestrationService.start_run`, and carried as a `force` argument on the MCP
  tool.
- A forced dispatch is *recorded*, per AC3, as a dedicated `Artifact` row with
  kind `stage_dispatch_override` whose title carries the stage key — the same
  durable-row pattern the counter itself uses, so the override survives a
  restart and is visible to a fresh session.
- The scope-reroute exemption (`ticket.scope_reroute_agent`) applies on the
  standalone paths exactly as it does in `enforce_stage_retry_budget`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.config import settings
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tools import TOOL_DEFINITIONS, execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    DispatchSurface,
    ExternalHarness,
    ParallelAgentSpec,
    QueuedRun,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
    Worktree,
)
from loregarden.services import conflict_resolution, queue_admission
from loregarden.services.git_automation import AutomationResult
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import GitAutomationConfig, RetryBudgetConfig
from loregarden.services.parallel_run_service import ParallelRunService
from loregarden.services.queue_admission import QueueAdmissionService
from loregarden.services.queue_dispatch import LaneDispatch
from loregarden.services.run_lease import AGENT_RUN_LEASE
from loregarden.services.run_service import RunService
from loregarden.services.stage_fanout_service import launch_fanout
from loregarden.services.stage_parking import park_stage
from loregarden.services.stage_retry_budget import (
    NO_RENEWER_DISPATCH_CEILING,
    StageRetryBudgetExceeded,
    blocked_on_stage_retry_budget,
    charge_fanout_dispatch,
    clear_stage_dispatches,
    count_stage_dispatches,
    enforce_stage_retry_budget,
    record_reroute_exempt_dispatch,
    record_stage_dispatch,
    record_stage_retry_block,
    stage_retry_block_message,
)
from loregarden.services.workflow_state import initial_stages_json, set_stage_status
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_orchestration_run
from tests.worktree_helpers import make_repo

BUDGET = 5
REVIEW_MEMBERS = ("gdscript_reviewer", "static_qa", "architecture_reviewer")

# The kind the forced-dispatch record is expected to use. Dedicated, like
# `_DISPATCH_KIND`, so it never collides with diff/log/test/evidence artifacts.
OVERRIDE_KIND = "stage_dispatch_override"


def _build_ticket(db_session: Session, *, parallel: bool = False):
    """A ticket on a throwaway "review" stage.

    Single-agent by default: the manual Run-stage path names no member, and
    `_resolve_run_agent` refuses a parallel stage started without an explicit
    `agent_id` ("a 'parallel' stage must … be started per member"). Only the
    trap-1 tests, which drive the per-member loop themselves, ask for the
    3-member version.

    An absolute, nonexistent `repo_path`: `repo_path="."` resolves against
    `settings.repo_root` (this actual checkout), and anything that reaches git
    through the workspace would then operate on the developer's own tree.
    """
    ws = Workspace(
        slug=f"standalone-budget-{uuid4()}",
        name="Standalone Retry Budget",
        repo_path="/nonexistent/standalone-retry-budget-repo",
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    review = (
        WorkflowStageDef(
            key="review",
            name="Review",
            stage_type="parallel",
            order=1,
            parallel_agents=[
                ParallelAgentSpec(agent_id=a, skill_name="review") for a in REVIEW_MEMBERS
            ],
        )
        if parallel
        else WorkflowStageDef(
            key="review",
            name="Review",
            stage_type="agent",
            order=1,
            agent_id="backend_implementer",
            skill_name="review",
        )
    )
    stages = [
        review,
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
    ]
    template = WorkflowTemplate(
        slug=f"standalone-budget-tpl-{uuid4()}",
        name="Standalone Retry Budget Template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": "review", "to": "done", "when": "pass"}]),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=f"standalone-budget-ticket-{uuid4()}",
        workspace_id=ws.id,
        title="Standalone retry budget",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="review",
        workflow_stage_status=StageStatus.PENDING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="review",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()
    return ticket


def _reset_stage_to_pending(db_session: Session, ticket: Ticket) -> None:
    """Put the stage back where a finished run leaves it, so the next start is a
    genuinely new dispatch pass rather than a member joining the running one.

    Deliberately `set_stage_status` and not `set_ticket_workflow`: the latter
    calls `refresh_stage_retry_budget` on a PENDING transition, which would wipe
    the very counter under test.
    """
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, "review", StageStatus.PENDING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()


def _agent_runs(db_session: Session, ticket_id: str) -> list[AgentRun]:
    return list(
        db_session.exec(
            select(AgentRun).where(AgentRun.ticket_id == ticket_id, AgentRun.stage_key == "review")
        ).all()
    )


def _override_records(db_session: Session, ticket_id: str, stage_key: str) -> list[Artifact]:
    return list(
        db_session.exec(
            select(Artifact).where(
                Artifact.ticket_id == ticket_id,
                Artifact.kind == OVERRIDE_KIND,
            )
        ).all()
    )


def _seed_exhausted_budget(db_session: Session, ticket_id: str, stage_key: str = "review") -> None:
    for _ in range(BUDGET):
        record_stage_dispatch(db_session, ticket_id, stage_key)


# -- AC1 / AC5: the standalone path consumes and honours the same counter ------


def test_a_standalone_dispatch_consumes_the_same_counter_as_an_orchestrated_one(
    db_session: Session,
):
    """AC1: one counter, keyed on (ticket, stage), whoever dispatched."""
    ticket = _build_ticket(db_session)
    assert count_stage_dispatches(db_session, ticket.id, "review") == 0

    RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


def test_the_sixth_standalone_dispatch_of_one_stage_is_refused(db_session: Session):
    """AC5, stated literally: dispatch one stage six times through the
    standalone path; the sixth is refused against the default budget of 5."""
    ticket = _build_ticket(db_session)
    service = RunService(db_session)

    for attempt in range(BUDGET):
        service.start_stage_execution(ticket, stage_key="review")
        db_session.refresh(ticket)
        _reset_stage_to_pending(db_session, ticket)
        assert count_stage_dispatches(db_session, ticket.id, "review") == attempt + 1

    with pytest.raises(ValueError) as refusal:
        service.start_stage_execution(ticket, stage_key="review")

    message = str(refusal.value)
    assert "review" in message
    assert str(BUDGET) in message  # AC2: names the count
    assert "force" in message.lower()  # AC2: names the override
    # AC2 again, from the outside: the refused sixth pass dispatched nothing.
    # Without this the test is satisfiable by an implementation that raises
    # *after* creating the run it was supposed to refuse.
    assert len(_agent_runs(db_session, ticket.id)) == BUDGET


def test_the_refusal_names_the_count_and_the_override_without_dispatching(db_session: Session):
    """AC2: a refusal, not a silent allow — no AgentRun is created."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    runs_before = len(_agent_runs(db_session, ticket.id))

    with pytest.raises(ValueError):
        RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert len(_agent_runs(db_session, ticket.id)) == runs_before
    # A refusal is not an override: nothing was forced, so the audit trail AC3
    # asks for must stay empty. Otherwise every refusal manufactures a record
    # that says a human deliberately spent the budget.
    assert _override_records(db_session, ticket.id, "review") == []


# -- TRAP 2: the refusal must not be a block, or it clears itself --------------


def test_the_refusal_leaves_the_ticket_unblocked_and_the_counter_intact(db_session: Session):
    """TRAP 2. `_prepare_stage_start` calls `refresh_stage_retry_budget` when
    the ticket or stage is BLOCKED, which wipes the counter. A refusal
    implemented as a ticket block is therefore self-clearing: the very next
    start finds a fresh budget and dispatches, and the breaker never holds.

    So: state untouched, and the counter still reads its exhausted value."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)

    with pytest.raises(ValueError):
        RunService(db_session).start_stage_execution(ticket, stage_key="review")

    db_session.refresh(ticket)
    assert ticket.state == TicketState.IN_PROGRESS
    assert ticket.blocking_issues == ""
    # PENDING exactly, not merely "not BLOCKED": a refusal raised *after*
    # `start_run` flipped the stage to RUNNING leaves the stage wedged running
    # with no agent behind it — the very state this ticket's own workflow got
    # stuck in. The check has to happen before any state is written.
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_a_second_attempt_after_a_refusal_is_refused_again(db_session: Session):
    """The self-clearing failure mode, caught from the outside: if the first
    refusal wiped the counter, this second call would sail through."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    service = RunService(db_session)

    with pytest.raises(ValueError):
        service.start_stage_execution(ticket, stage_key="review")
    db_session.refresh(ticket)
    with pytest.raises(ValueError):
        service.start_stage_execution(ticket, stage_key="review")

    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET
    assert _agent_runs(db_session, ticket.id) == []


# -- AC3: the force override ---------------------------------------------------


def test_force_overrides_the_refusal_and_is_recorded(db_session: Session):
    """AC3. A person deliberately re-running a stage is not the failure mode
    this breaker exists to stop, so the refusal must be overridable — and the
    override must leave a durable trace, since it spends a budget a human chose
    to ignore."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review", force=True)

    assert run is not None
    assert run.stage_key == "review"
    overrides = _override_records(db_session, ticket.id, "review")
    assert len(overrides) == 1
    assert "review" in overrides[0].title


def test_force_still_consumes_the_counter(db_session: Session):
    """An override is not an exemption: the forced pass is still a dispatch, so
    it counts. Otherwise a UI that always forces has no counter at all."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)

    RunService(db_session).start_stage_execution(ticket, stage_key="review", force=True)

    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET + 1


def test_force_is_not_needed_while_the_stage_is_within_budget(db_session: Session):
    """No override record is written for an ordinary dispatch — the trace has to
    mean something when it is there."""
    ticket = _build_ticket(db_session)

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review")

    # Assert the dispatch really happened as well, or this guard is satisfied
    # by an implementation that refuses everything and writes no records.
    assert run is not None
    assert len(_agent_runs(db_session, ticket.id)) == 1
    assert _override_records(db_session, ticket.id, "review") == []


# -- TRAP 1: 105 AC1.3, a parallel stage is one attempt ------------------------


def test_a_three_member_parallel_dispatch_counts_as_one_attempt(db_session: Session):
    """TRAP 1 / ticket 105 AC1.3.

    `_prepare_stage_start` runs once per parallel *member*, not once per stage
    dispatch: `_start_parallel_stage_runs` loops `start_run` once per spec (and
    `external_harness` / `stage_fanout_service` do the same). Counting there
    naively makes a 3-member stage cost 3 attempts and burns a 5-attempt budget
    in two passes.

    This mirrors that loop exactly: three `start_run` calls, one per member, for
    a single dispatch pass. The counter must move by one.
    """
    ticket = _build_ticket(db_session, parallel=True)
    orch = OrchestrationService(db_session)

    for agent_id in REVIEW_MEMBERS:
        orch.start_run(ticket, stage_key="review", agent_id=agent_id, skill_name="review")
        db_session.refresh(ticket)

    assert len(_agent_runs(db_session, ticket.id)) == len(REVIEW_MEMBERS)
    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


def test_two_parallel_passes_cost_two_attempts_not_six(db_session: Session):
    """The same property one level up: the budget must survive more than one
    pass of a 3-member stage."""
    ticket = _build_ticket(db_session, parallel=True)
    orch = OrchestrationService(db_session)

    for _ in range(2):
        for agent_id in REVIEW_MEMBERS:
            orch.start_run(ticket, stage_key="review", agent_id=agent_id, skill_name="review")
            db_session.refresh(ticket)
        _reset_stage_to_pending(db_session, ticket)

    assert count_stage_dispatches(db_session, ticket.id, "review") == 2


def test_the_last_affordable_parallel_pass_dispatches_all_three_members(db_session: Session):
    """TRAP 1, the half that grouping the *record* alone does not fix.

    With four dispatches already spent, this pass is the fifth and last one the
    budget affords. Member 1 records it, taking the counter to exactly the
    budget — so a check that reads the counter per member sees an exhausted
    budget for members 2 and 3 and refuses them, tearing the final parallel
    stage in half and leaving one lane running alone.

    Grouping only the write is therefore not enough: the *check* has to be
    grouped too (a member joining an already-RUNNING pass is not a new
    dispatch). All three members must start, and the pass must cost one.
    """
    ticket = _build_ticket(db_session, parallel=True)
    for _ in range(BUDGET - 1):
        record_stage_dispatch(db_session, ticket.id, "review")
    orch = OrchestrationService(db_session)

    for agent_id in REVIEW_MEMBERS:
        orch.start_run(ticket, stage_key="review", agent_id=agent_id, skill_name="review")
        db_session.refresh(ticket)

    assert len(_agent_runs(db_session, ticket.id)) == len(REVIEW_MEMBERS)
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_the_budget_is_keyed_per_stage_not_per_ticket(db_session: Session):
    """AC1 names a per-(ticket, stage) counter. Every other test in this file
    drives a single stage, so all of them are satisfied by an implementation
    that counts every dispatch marker on the ticket regardless of stage — which
    would refuse a stage the ticket has never dispatched once a *sibling* stage
    exhausted its own budget.

    A regression guard: it passes today (nothing counts at all) and must keep
    passing once the check exists.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id, stage_key="spec")

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert run is not None
    assert count_stage_dispatches(db_session, ticket.id, "spec") == BUDGET


# -- TRAP 3: MCP loregarden_start_stage ---------------------------------------


def _start_stage_via_mcp(db_session: Session, orch_run_id: str, **extra) -> dict:
    args = {"run_id": orch_run_id, "stage_key": "review", **extra}
    return json.loads(
        execute_tool(
            db_session,
            "loregarden_start_stage",
            normalize_tool_arguments("loregarden_start_stage", args),
        )
    )


def test_mcp_start_stage_is_refused_once_the_budget_is_exhausted(db_session: Session):
    """TRAP 3. `loregarden_start_stage` reaches neither the orchestrator loop
    nor `start_run`: it calls `OrchestrationCallbackService.start_stage`, which
    only flips stage status and publishes an event, and creates no AgentRun at
    all. It is the path AC1 explicitly names, so it needs its own check site."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    with pytest.raises(ValueError) as refusal:
        _start_stage_via_mcp(db_session, orch_run.id)

    message = str(refusal.value)
    assert str(BUDGET) in message
    assert "force" in message.lower()
    db_session.refresh(ticket)
    assert ticket.state == TicketState.IN_PROGRESS
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET
    # `start_stage` writes stage status before it publishes anything, so a check
    # bolted on downstream of it would refuse having already flipped the stage
    # to RUNNING — the wedged-stage failure mode again, and on this path there
    # is no AgentRun to make it visible.
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert _override_records(db_session, ticket.id, "review") == []


def test_mcp_start_stage_consumes_the_counter(db_session: Session):
    """AC1: the MCP path shares the counter it is now bounded by."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )

    result = _start_stage_via_mcp(db_session, orch_run.id)

    assert result.get("ok") is True
    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


def test_mcp_start_stage_force_overrides_and_is_recorded(db_session: Session):
    """AC3 on the MCP path. The override arrives as a tool argument, so
    `_normalize_stage_scoped` has to carry `force` through — a normalizer that
    silently drops it leaves the caller with no way to override at all."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    result = _start_stage_via_mcp(db_session, orch_run.id, force=True)

    assert result.get("ok") is True
    assert len(_override_records(db_session, ticket.id, "review")) == 1
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET + 1


def test_the_mcp_tool_schema_declares_the_force_override():
    """AC3 on the MCP path, one level below the call.

    `normalize_tool_arguments` runs `_apply_aliases`, which consults
    `_declared_properties` — and an MCP client only sends arguments the
    advertised `inputSchema` declares. A `force` the normalizer carries but the
    schema never advertises is unreachable from a real client, so the override
    would exist only for this test file.
    """
    schema = next(t["inputSchema"] for t in TOOL_DEFINITIONS if t["name"] == McpTool.START_STAGE)

    assert "force" in schema["properties"]


# -- AC4: the orchestrated path is unchanged ----------------------------------


def test_the_orchestrated_check_still_blocks_rather_than_raising(db_session: Session):
    """AC4. The orchestrator has an orchestration run to park the failure on, so
    its exhausted-budget behaviour stays a block — unlike the standalone
    refusal, which has nowhere to put a block that would not clear itself."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    blocked = enforce_stage_retry_budget(
        db_session,
        OrchestrationCallbackService(db_session),
        orch_run,
        ticket,
        "review",
        RetryBudgetConfig(enabled=True, max_attempts_per_stage=BUDGET),
    )

    assert blocked is not None
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert "review" in ticket.blocking_issues


def test_one_orchestrated_parallel_pass_is_not_counted_twice(db_session: Session):
    """AC4, the double-count regression. The orchestrator loop already calls
    `record_stage_dispatch` via `enforce_stage_retry_budget` before dispatching;
    if the shared seam records as well, every orchestrated pass would cost two
    attempts and halve the budget ticket 105 set."""
    ticket = _build_ticket(db_session, parallel=True)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    config = RetryBudgetConfig(enabled=True, max_attempts_per_stage=BUDGET)

    enforce_stage_retry_budget(
        db_session,
        OrchestrationCallbackService(db_session),
        orch_run,
        ticket,
        "review",
        config,
    )
    for agent_id in REVIEW_MEMBERS:
        OrchestrationService(db_session).start_run(
            ticket,
            stage_key="review",
            orchestration_run_id=orch_run.id,
            agent_id=agent_id,
            skill_name="review",
        )
        db_session.refresh(ticket)

    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


# -- the budget is the configured one, not a literal --------------------------


def _profile_with_retry_budget(tmp_path: Path, body: str):
    """Point profile resolution at a throwaway `default.yaml` carrying `body`
    under `retry_budget:`.

    Driven through the profile file rather than by patching a resolver name:
    the standalone check may read its config anywhere, and mocking one import
    site would pin *where* the profile is resolved instead of *that* the
    resolved profile is honoured. The workspaces these tests build have unique
    slugs, so `resolve_orchestration_profile` falls through to `default.yaml`.
    """
    root = tmp_path / "agent_context" / "orchestration"
    root.mkdir(parents=True, exist_ok=True)
    (root / "default.yaml").write_text(
        f"slug: default\nname: Test Profile\nretry_budget:\n{body}", encoding="utf-8"
    )
    return mock.patch.object(settings, "repo_root", tmp_path)


def test_the_configured_budget_is_honoured_not_a_hard_coded_five(
    db_session: Session, tmp_path: Path
):
    """Every other test here runs against the default budget of 5, so all of
    them are satisfied by an implementation that compares against the literal
    5 and never reads `profile.retry_budget` at all. AC1 asks the standalone
    path to honour *the same* budget as the orchestrated one, which is the
    configured one: a workspace that sets 2 gets 2, and the message says 2.
    """
    ticket = _build_ticket(db_session)
    service = RunService(db_session)

    with _profile_with_retry_budget(tmp_path, "  enabled: true\n  max_attempts_per_stage: 2\n"):
        for _ in range(2):
            service.start_stage_execution(ticket, stage_key="review")
            db_session.refresh(ticket)
            _reset_stage_to_pending(db_session, ticket)

        with pytest.raises(ValueError) as refusal:
            service.start_stage_execution(ticket, stage_key="review")

    assert "2" in str(refusal.value)
    assert count_stage_dispatches(db_session, ticket.id, "review") == 2


def test_a_disabled_budget_refuses_nothing_on_the_standalone_path(
    db_session: Session, tmp_path: Path
):
    """`RetryBudgetConfig.enabled` is the existing kill switch, and
    `exceeds_stage_retry_budget` already returns "" when it is off. The
    standalone check must respect it too, or turning the breaker off leaves
    half of it armed with no way to reach it.

    A regression guard: passes today, and must keep passing.
    """
    ticket = _build_ticket(db_session)
    service = RunService(db_session)

    with _profile_with_retry_budget(tmp_path, "  enabled: false\n  max_attempts_per_stage: 2\n"):
        for _ in range(BUDGET + 1):
            assert service.start_stage_execution(ticket, stage_key="review") is not None
            db_session.refresh(ticket)
            _reset_stage_to_pending(db_session, ticket)

    assert len(_agent_runs(db_session, ticket.id)) == BUDGET + 1


# -- exemptions that must survive ---------------------------------------------


def test_a_pending_scope_reroute_is_exempt_on_the_standalone_path(db_session: Session):
    """A pending scope-denial reroute is a handoff to the *sibling* implementer,
    not a retry of the same failing work. It has its own bound (the
    scope-reroute ledger) and is already exempt in
    `enforce_stage_retry_budget`; the standalone check must exempt it too, or
    the sibling is refused before it ever runs."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    ticket.scope_reroute_agent = "backend_implementer"
    db_session.add(ticket)
    db_session.commit()

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review")

    # The dispatch is real, not a stub return: `run is not None` alone is also
    # true of a short-circuit that never reaches start_run.
    assert run is not None
    assert len(_agent_runs(db_session, ticket.id)) == 1
    # Exempt "exactly as in `enforce_stage_retry_budget`" (stage_retry_budget.py)
    # means exempt from the write as well as the read: that function returns
    # before `record_stage_dispatch`. A reroute that consumed the budget would
    # spend a stage's limit on work that has its own separate ledger.
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET
    # And an exemption is not a forced override — no audit record.
    assert _override_records(db_session, ticket.id, "review") == []


def test_a_single_agent_orchestrated_dispatch_is_still_counted_once(db_session: Session):
    """AC4 for the ordinary, non-parallel orchestrated stage — the shape most
    stages actually have. The orchestrator loop records the pass itself via
    `enforce_stage_retry_budget`; if the shared seam records again, every
    orchestrated dispatch costs two and the budget ticket 105 set is halved.

    A regression guard: passes today, and must keep passing.
    """
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )

    enforce_stage_retry_budget(
        db_session,
        OrchestrationCallbackService(db_session),
        orch_run,
        ticket,
        "review",
        RetryBudgetConfig(enabled=True, max_attempts_per_stage=BUDGET),
    )
    OrchestrationService(db_session).start_run(
        ticket, stage_key="review", orchestration_run_id=orch_run.id
    )

    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


# -- the guard must not escape into paths that cannot absorb a refusal ---------


def _conflicted_worktree(db_session: Session, ticket: Ticket) -> tuple[AgentRun, Worktree]:
    """A finished run whose worktree git has left mid-merge."""
    run = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        stage_key="review",
        agent_id="backend_implementer",
    )
    worktree = Worktree(
        workspace_id=ticket.workspace_id,
        agent_run_id=run.id,
        parent_branch="main",
        worktree_path="/nonexistent/standalone-retry-budget-repo/wt",
        branch="loregarden/standalone-budget",
        has_conflicts=True,
    )
    worktree.conflict_files = ["answer.py"]
    db_session.add(worktree)
    db_session.commit()
    run.worktree_id = worktree.id
    db_session.add(run)
    db_session.commit()
    return run, worktree


def _no_real_git():
    """conflict_resolution reaches git twice; neither repo exists in a test."""
    return (
        mock.patch.object(conflict_resolution, "conflicted_files", return_value=["answer.py"]),
        mock.patch.object(conflict_resolution, "_conflict_excerpt", return_value="diff"),
        mock.patch("loregarden.services.run_service.schedule_agent_run"),
    )


def test_an_exhausted_budget_does_not_raise_out_of_conflict_resolution(db_session: Session):
    """DEFECT 1. `_dispatch_resolver` calls `start_run` with no
    `orchestration_run_id`, so it now falls inside the standalone guard. Its
    caller — `ParallelRunService._publish_run_work` — documents "a failure here
    is reported, never raised", and the slot-freeing in
    `on_parallel_run_complete` sits *after* the throw point, so a refusal
    escaping here strands a parallel execution slot.

    The refusal must therefore be caught and reported, not raised."""
    ticket = _build_ticket(db_session)
    run, worktree = _conflicted_worktree(db_session, ticket)
    _seed_exhausted_budget(db_session, ticket.id)
    runs_before = len(_agent_runs(db_session, ticket.id))

    files_patch, excerpt_patch, schedule_patch = _no_real_git()
    with files_patch, excerpt_patch, schedule_patch as schedule:
        report = conflict_resolution.request_agent_resolution(
            db_session,
            run,
            ticket,
            worktree,
            Path("/nonexistent/standalone-retry-budget-repo/wt"),
            max_attempts=2,
        )

    # Reported, not raised: the report exists and says no resolver ran.
    assert report is not None
    assert report.resolution_attempted is False
    assert len(_agent_runs(db_session, ticket.id)) == runs_before
    schedule.assert_not_called()


def test_a_refused_resolver_is_reported_as_a_failed_automation_result(db_session: Session):
    """DEFECT 1, one level up. `_handle_merge_conflicts` must turn the refusal
    into an `ok: False` result — that is the branch `on_parallel_run_complete`
    reads to free the slot and drain the queue. An `ok: True` "resolution
    dispatched" for a resolver that was never dispatched would be a lie, and an
    exception would skip the slot-free entirely."""
    ticket = _build_ticket(db_session)
    run, worktree = _conflicted_worktree(db_session, ticket)
    workspace = db_session.get(Workspace, ticket.workspace_id)
    _seed_exhausted_budget(db_session, ticket.id)

    files_patch, excerpt_patch, schedule_patch = _no_real_git()
    with files_patch, excerpt_patch, schedule_patch:
        outcome = ParallelRunService(db_session)._handle_merge_conflicts(
            run,
            ticket,
            workspace,
            worktree,
            GitAutomationConfig(auto_resolve_conflicts=True, max_conflict_resolve_attempts=2),
            AutomationResult(),
        )

    assert outcome["ok"] is False
    assert outcome.get("resolving_conflicts") is not True


# -- DEFECT 2: a deliberate fan-out costs exactly one attempt ------------------


def test_a_stage_fanout_costs_exactly_one_dispatch(db_session: Session, tmp_path: Path):
    """`launch_fanout` finalizes the stage to RUNNING *before* its per-attempt
    `start_run` loop, so every attempt reaches the guard with
    `stage_already_running=True` and the guard returns before recording. A
    deliberate fan-out therefore costs nothing at all.

    The agreed contract is one attempt per fan-out: not N (which would burn a
    5-attempt budget in two fan-outs) and not zero (which takes a whole
    dispatch path back outside the counter this ticket exists to install)."""
    repo = make_repo(tmp_path)
    workspace = Workspace(
        slug=f"fanout-budget-{uuid4()}", name="Fan-out budget", repo_path=str(repo)
    )
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)

    stages = [
        WorkflowStageDef(
            key="implement",
            name="Implement",
            stage_type="agent",
            order=1,
            agent_id="backend_implementer",
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
    ]
    template = WorkflowTemplate(
        slug=f"fanout-budget-tpl-{uuid4()}",
        name="Fan-out budget template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": "implement", "to": "done", "when": "pass"}]),
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ticket = Ticket(
        external_id=f"fanout-budget-{uuid4()}",
        workspace_id=workspace.id,
        title="Fan-out budget",
        branch="loregarden/fanout-budget",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.PENDING,
        next_agent="backend_implementer",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()

    def _execute(self, run, ticket, *, advance_workflow=True, skip_git_branch=False):
        worktree = self.session.get(Worktree, run.worktree_id)
        (Path(worktree.worktree_path) / "answer.txt").write_text(f"{worktree.branch}\n")
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=(
                '<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "pass", "confidence": 0.9}\n'
                "<<<END_STAGE_REPORT>>>\n"
            ),
            stderr="",
            advance_workflow=False,
        )

    with mock.patch.object(CliAgentExecutor, "execute", _execute):
        launch_fanout(db_session, ticket, "implement", 2)

    assert count_stage_dispatches(db_session, ticket.id, "implement") == 1


# -- DEFECT 3: the dispatch that clears the block is the first of the new budget


def test_the_start_that_clears_the_breakers_block_counts_as_one(db_session: Session):
    """`_prepare_stage_start` clears the counter when a human re-enters a stage
    the breaker itself blocked — that is the intended fresh budget. But the
    start doing the clearing is itself a dispatch, so skipping the guard for it
    hands the operator six more dispatches before the next refusal instead of
    five."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = stage_retry_block_message("review", BUDGET, BUDGET)
    db_session.add(ticket)
    db_session.commit()
    # The block is the breaker's own, so it is marked structurally — the prose
    # is no longer read at all (see
    # `test_prose_at_budget_still_cannot_hand_a_stage_a_fresh_counter`).
    record_stage_retry_block(db_session, ticket.id, "review")

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert run is not None
    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


# =============================================================================
# Review pass 3. Each test below fails against the implementation reviewed and
# passes against the one that replaced it; the finding it pins is named in the
# docstring.
# =============================================================================


def _blocked_for_another_reason(db_session: Session, ticket: Ticket, message: str) -> None:
    """Block the ticket for something that is not the retry breaker."""
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = message
    db_session.add(ticket)
    db_session.commit()


# -- CRITICAL: the breaker must not be self-disableable by the agent it stops --


def test_an_orchestrated_agent_cannot_force_past_its_own_retry_budget(db_session: Session):
    """CRITICAL. `force` is an argument on `loregarden_start_stage`, and
    `START_STAGE` is not in `ORCHESTRATED_DENIED_MCP_TOOLS` — a stage agent may
    legitimately start stages. On an `auto_approve` run the permission bridge
    blanket-approves every non-denied tool and writes no `approvals` row, so a
    looping agent could call `start_stage(force=true)` forever, leaving no audit
    trace of having cleared the circuit breaker that stopped it.

    Refused in `execute_tool`, keyed on the `orchestrated` flag already threaded
    there — not only in the permission bridge, which a direct `/mcp` POST never
    reaches.
    """
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    with pytest.raises(ValueError) as denial:
        execute_tool(
            db_session,
            "loregarden_start_stage",
            normalize_tool_arguments(
                "loregarden_start_stage",
                {"run_id": orch_run.id, "stage_key": "review", "force": True},
            ),
            orchestrated=True,
        )

    assert "force" in str(denial.value)
    db_session.refresh(ticket)
    # Nothing was spent and nothing was started: the denial is not a forced
    # dispatch that merely reported itself.
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET
    assert _override_records(db_session, ticket.id, "review") == []
    assert ticket.workflow_stage_status == StageStatus.PENDING


def test_an_orchestrated_agent_may_still_start_a_stage_within_budget(db_session: Session):
    """The denial is argument-scoped, not tool-scoped: an orchestrated stage
    agent starting the next stage is ordinary pipeline work, and denying the
    whole tool would break the pipeline to close the loophole."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )

    result = json.loads(
        execute_tool(
            db_session,
            "loregarden_start_stage",
            normalize_tool_arguments(
                "loregarden_start_stage", {"run_id": orch_run.id, "stage_key": "review"}
            ),
            orchestrated=True,
        )
    )

    assert result.get("ok") is True
    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


def test_a_human_on_the_mcp_path_keeps_the_force_override(db_session: Session):
    """The same call from a caller that is not an orchestrated pipeline agent —
    a human's own terminal session, Ticket Studio chat, an operator's curl —
    still forces. The breaker stops runaway agents, not people."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    result = _start_stage_via_mcp(db_session, orch_run.id, force=True)

    assert result.get("ok") is True
    assert len(_override_records(db_session, ticket.id, "review")) == 1


# -- HIGH 1 / HIGH 2: the refusal writes nothing ------------------------------


def test_the_refusal_leaves_a_differently_blocked_ticket_exactly_as_it_found_it(
    db_session: Session,
):
    """HIGH 1 and HIGH 2 together.

    `test_the_refusal_leaves_the_ticket_unblocked_and_the_counter_intact` asserts
    IN_PROGRESS and empty `blocking_issues` on a fixture that already holds both,
    so it cannot observe the side effect: `_prepare_stage_start` clears
    `blocking_issues` and unblocks the ticket *before* the guard raises, and the
    API's `reservation.release()` commits it. Over real HTTP that returned 409
    and left the ticket IN_PROGRESS with the operator's blocking diagnosis
    erased.

    The discriminating case is a ticket blocked for a reason that is *not* this
    breaker, with an exhausted counter — the `budget_block == False` branch
    nothing else exercises. The refusal must leave it exactly as it was, and
    the commit that follows the refusal must have nothing to persist.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    diagnosis = "Stage was left running with no agent run behind it. Re-run to continue."
    _blocked_for_another_reason(db_session, ticket, diagnosis)

    with pytest.raises(ValueError):
        RunService(db_session).start_stage_execution(ticket, stage_key="review")

    # Exactly what `api.tickets.start_run` does on the refusal path.
    db_session.commit()
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert ticket.blocking_issues == diagnosis
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET
    assert _agent_runs(db_session, ticket.id) == []


# -- HIGH 3: force survives the queue ----------------------------------------


def _queued_stage_entry(db_session: Session, ticket: Ticket, *, force: bool) -> QueuedRun:
    entry = QueuedRun(
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        slot_number=1,
        entry_kind="stage",
        stage_key="review",
        force=force,
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    return entry


def test_a_forced_stage_start_survives_being_parked_in_a_lane(db_session: Session):
    """HIGH 3. `QueuedRun` had no `force` column, so admission dropped the
    override on the floor: when the lane reached the entry, `dispatch_stage`
    started the stage without it, the budget refused, and the pre-existing
    `except ValueError` logged a warning and returned None. A deliberate human
    override died invisibly at promotion, and whether it survived depended on
    how busy the box happened to be.

    Threaded through rather than evaluated at admission, for the same reason
    `driver`, `max_stages` and `timeout_seconds` are: the entry is the only
    record of the ask by the time a lane reaches it.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    entry = _queued_stage_entry(db_session, ticket, force=True)

    with mock.patch("loregarden.services.queue_dispatch.schedule_agent_run"):
        run = LaneDispatch(db_session).dispatch_stage(ticket, entry)

    assert run is not None
    assert len(_override_records(db_session, ticket.id, "review")) == 1
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET + 1


def test_an_unforced_lane_dispatch_records_why_it_refused(db_session: Session):
    """The other half of HIGH 3: a refusal at promotion reached only the server
    log. The lane card is where an operator finds out their stage never
    started, so the reason goes on the entry."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    entry = _queued_stage_entry(db_session, ticket, force=False)

    with mock.patch("loregarden.services.queue_dispatch.schedule_agent_run"):
        run = LaneDispatch(db_session).dispatch_stage(ticket, entry)

    assert run is None
    db_session.refresh(entry)
    assert "retry budget" in entry.failure_reason
    assert entry.last_failed_at is not None


def test_admission_parks_the_force_override_with_the_entry(db_session: Session):
    """And the half above that: the parked entry has to be given the override
    in the first place."""
    ticket = _build_ticket(db_session)

    with mock.patch.object(queue_admission, "claim_free_slot", return_value=None):
        reservation = QueueAdmissionService(db_session).reserve_stage(
            ticket, stage_key="review", force=True
        )

    assert reservation.admitted is False
    entry = db_session.exec(select(QueuedRun).where(QueuedRun.ticket_id == ticket.id)).one()
    assert entry.force is True
    assert entry.stage_key == "review"


# -- HIGH 4: the block marker is structural, not a phrase in prose ------------


def test_blocking_prose_alone_does_not_earn_a_stage_a_fresh_budget(db_session: Session):
    """HIGH 4. Whether the counter resets was decided by
    `"retry budget" in blocking_issues.lower()` — a substring of English, on a
    field half the control plane writes and `loregarden_block_ticket` lets an
    agent write itself. A stage two dispatches in could block itself with the
    right words and start again with a full budget.

    The mark is now structural (an artifact this module writes with the block),
    and it is the only thing that counts. There is no prose fallback at all —
    blocks recorded before the mark existed are given one by migration
    `0102_backfill_stage_retry_block`, not recognised by their text.
    """
    ticket = _build_ticket(db_session)
    for _ in range(2):
        record_stage_dispatch(db_session, ticket.id, "review")
    _blocked_for_another_reason(
        db_session, ticket, "The retry budget is fine; this is a different problem."
    )

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert run is not None
    # 2 kept + this dispatch. A reset would have left exactly 1.
    assert count_stage_dispatches(db_session, ticket.id, "review") == 3


def test_the_breakers_own_block_is_recorded_structurally_and_still_resets(db_session: Session):
    """The other side of HIGH 4: the block this breaker writes itself is marked
    where nothing else can forge it, and re-entering that stage still hands it
    a fresh budget."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    enforce_stage_retry_budget(
        db_session,
        OrchestrationCallbackService(db_session),
        orch_run,
        ticket,
        "review",
        RetryBudgetConfig(enabled=True, max_attempts_per_stage=BUDGET),
    )
    db_session.refresh(ticket)
    assert blocked_on_stage_retry_budget(db_session, ticket, "review") is True
    # Not the prose: the mark survives the text being rewritten.
    ticket.blocking_issues = "an operator retyped this"
    db_session.add(ticket)
    db_session.commit()
    assert blocked_on_stage_retry_budget(db_session, ticket, "review") is True

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert run is not None
    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


# -- HIGH 5: the override record says who forced it ---------------------------


def test_a_forced_dispatch_records_who_forced_it(db_session: Session):
    """HIGH 5. The override row carried ticket_id / kind / title and nothing
    else, so a human's click and an agent's own `start_stage` were byte-identical
    after the fact — on the one record whose entire purpose is accountability."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    _start_stage_via_mcp(db_session, orch_run.id, force=True, agent_id="backend_implementer")

    record = _override_records(db_session, ticket.id, "review")[0]
    payload = json.loads(record.content_json)
    assert payload["surface"] == DispatchSurface.MCP.value
    assert payload["orchestration_run_id"] == orch_run.id
    assert payload["agent_id"] == "backend_implementer"
    assert payload["stage_key"] == "review"
    assert payload["forced_at"]


def test_the_run_stage_path_records_a_different_surface(db_session: Session):
    """Attribution that cannot distinguish the two surfaces is not attribution."""
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)

    RunService(db_session).start_stage_execution(ticket, stage_key="review", force=True)

    payload = json.loads(_override_records(db_session, ticket.id, "review")[0].content_json)
    assert payload["surface"] == DispatchSurface.HTTP.value


# -- HIGH 6: a stranded RUNNING stage is not a free dispatch ------------------


def test_a_stage_left_running_by_a_dead_run_does_not_get_a_free_dispatch(db_session: Session):
    """HIGH 6. The guard returned before both the check and the record whenever
    the stage was already RUNNING — the grouping that makes a parallel stage
    cost one pass. But a stage left RUNNING by a run that died is not a pass in
    flight, and it is exactly the state a runaway leaves behind: measured, it
    bought exactly one free, unchecked dispatch before counting resumed.

    The grouping is now keyed on a pass that is actually in flight — a run of
    this stage still RUNNING, or an open fan-out group — rather than on stage
    status alone.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, "review", StageStatus.RUNNING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    assert _agent_runs(db_session, ticket.id) == []

    with pytest.raises(ValueError) as refusal:
        RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert "force" in str(refusal.value).lower()
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_a_stranded_running_stage_within_budget_is_counted(db_session: Session):
    """The same seam from the other side: the free pass was uncounted as well as
    unchecked, so a stranded stage could be redispatched without ever moving the
    counter toward the refusal."""
    ticket = _build_ticket(db_session)
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, "review", StageStatus.RUNNING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()

    RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert count_stage_dispatches(db_session, ticket.id, "review") == 1


# -- MEDIUM: the scope-reroute exemption is bounded ---------------------------


def test_an_unconsumed_scope_reroute_pin_does_not_grant_unlimited_dispatches(
    db_session: Session,
):
    """MEDIUM. `tickets.scope_reroute_agent` self-clears only when a dispatch
    actually picks the pinned agent. A pin nothing ever consumes — the stage
    does not carry that agent, the classifier keeps routing elsewhere — was an
    unlimited supply of free, uncounted dispatches through both enforcement
    paths, which is precisely the runaway the module exists to stop.

    Bounded at the same configured number of attempts as the budget itself.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    ticket.scope_reroute_agent = "an_agent_this_stage_never_dispatches"
    db_session.add(ticket)
    db_session.commit()
    service = RunService(db_session)

    for _ in range(BUDGET):
        assert service.start_stage_execution(ticket, stage_key="review") is not None
        db_session.refresh(ticket)
        assert ticket.scope_reroute_agent == "an_agent_this_stage_never_dispatches"
        _reset_stage_to_pending(db_session, ticket)

    with pytest.raises(ValueError):
        service.start_stage_execution(ticket, stage_key="review")


def test_a_fresh_budget_also_refills_the_reroute_exemption(db_session: Session):
    """The bound must not outlive the counter it shadows: a human who resets the
    stage and gets a fresh budget would otherwise find the sibling handoff
    permanently refused."""
    ticket = _build_ticket(db_session)
    for _ in range(BUDGET):
        record_reroute_exempt_dispatch(db_session, ticket.id, "review")
    _seed_exhausted_budget(db_session, ticket.id)
    ticket.scope_reroute_agent = "an_agent_this_stage_never_dispatches"
    db_session.add(ticket)
    db_session.commit()

    clear_stage_dispatches(db_session, ticket.id, "review")

    run = RunService(db_session).start_stage_execution(ticket, stage_key="review")
    assert run is not None
    # Exempt again, so the dispatch cost nothing.
    assert count_stage_dispatches(db_session, ticket.id, "review") == 0


# -- AC4: which block earns a fresh counter, on every path -------------------


def test_an_orchestrated_start_does_not_clear_the_counter_for_a_foreign_block(
    db_session: Session,
):
    """A DELIBERATE REVERSAL of the contract this test used to pin.

    It previously asserted that an orchestrated start clears the counter for
    *any* block, on the premise that reaching BLOCKED is a human act — so
    re-entering a blocked stage under the orchestrator was already a human
    decision to continue, and AC4 said the orchestrated path was unchanged.

    That premise is false. `loregarden_block_ticket` is granted to every agent
    and is not on `ORCHESTRATED_DENIED_MCP_TOOLS`, and
    `OrchestrationCallbackService.block_ticket` sets both the ticket and the
    stage BLOCKED. So an agent at 5/5 can block itself, restart orchestration
    (`loregarden_start_orchestration`, also granted), and have the unconditional
    orchestrated reset wipe the dispatch counter *and* the reroute ledger — with
    no force decision and no `stage_dispatch_override` audit row. The reset is
    now gated on `blocked_on_stage_retry_budget` on every path: only the
    breaker's own block earns one.

    `test_the_orchestrated_path_still_resets_after_the_breakers_own_block`
    below pins the half that must keep resetting.
    """
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)
    _blocked_for_another_reason(db_session, ticket, "a gate the agent cannot fix")

    OrchestrationService(db_session).start_run(
        ticket, stage_key="review", orchestration_run_id=orch_run.id
    )

    # Kept, and not re-recorded: the loop counts its own pass.
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_an_agent_blocking_itself_cannot_clear_the_counter_by_restarting(
    db_session: Session,
):
    """The attack the reversal above closes, driven through the real seam.

    `OrchestrationCallbackService.block_ticket` is what
    `loregarden_block_ticket` calls: an agent at its budget blocks itself with
    prose of its own choosing, which ends the orchestration run, and then starts
    a fresh one. Before the gate, the next orchestrated `start_run` cleared the
    counter unconditionally — an agent-reachable reset of the breaker meant to
    bound that very agent.
    """
    ticket = _build_ticket(db_session)
    blocking_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    OrchestrationCallbackService(db_session).block_ticket(
        blocking_run,
        ticket,
        stage_key="review",
        message="I need a human to look at this.",
    )
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert blocked_on_stage_retry_budget(db_session, ticket, "review") is False

    restarted = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    OrchestrationService(db_session).start_run(
        ticket, stage_key="review", orchestration_run_id=restarted.id
    )

    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_the_orchestrated_path_still_resets_after_the_breakers_own_block(
    db_session: Session,
):
    """The other half of the gate: a stage the breaker itself blocked still gets
    a fresh budget when the orchestrator re-enters it, which is the behaviour
    ticket 105 shipped and AC4 protects."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)

    enforce_stage_retry_budget(
        db_session,
        OrchestrationCallbackService(db_session),
        orch_run,
        ticket,
        "review",
        RetryBudgetConfig(enabled=True, max_attempts_per_stage=BUDGET),
    )
    db_session.refresh(ticket)
    assert blocked_on_stage_retry_budget(db_session, ticket, "review") is True

    restarted = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    OrchestrationService(db_session).start_run(
        ticket, stage_key="review", orchestration_run_id=restarted.id
    )

    assert count_stage_dispatches(db_session, ticket.id, "review") == 0


# -- MEDIUM: a refused resolver spends nothing --------------------------------


def test_a_refused_resolver_spends_no_conflict_attempt_and_no_rework_entry(
    db_session: Session,
):
    """MEDIUM. The ConflictReport and the rework-ledger entry were both written
    before the refusal was discovered, so a resolver that never went out still
    burned one of the two conflict-resolution attempts and left feedback
    addressed to an agent that was never dispatched. The conflict report itself
    stays — the conflict really happened, and the caller reads it — but it no
    longer counts as an attempt at resolving it."""
    ticket = _build_ticket(db_session)
    run, worktree = _conflicted_worktree(db_session, ticket)
    _seed_exhausted_budget(db_session, ticket.id)

    files_patch, excerpt_patch, schedule_patch = _no_real_git()
    with (
        files_patch,
        excerpt_patch,
        schedule_patch,
        mock.patch.object(conflict_resolution, "record_rework_feedback") as ledger,
    ):
        report = conflict_resolution.request_agent_resolution(
            db_session, run, ticket, worktree, Path("/nonexistent/wt"), max_attempts=2
        )

    assert report is not None
    assert report.resolution_attempted is False
    ledger.assert_not_called()
    assert conflict_resolution.attempts_so_far(db_session, worktree) == 0


# -- MEDIUM: the fan-out policy is a stated decision, not an accident ---------


def test_a_fanout_charge_costs_one_dispatch_and_refuses_nothing(db_session: Session):
    """MEDIUM. `stage_fanout_service` recorded a dispatch but never consulted
    the counter, so `POST /stage-fanout` was never refused however exhausted the
    stage was — and each launch spawns up to `MAX_ATTEMPTS` subprocesses.

    That is the right policy (a fan-out is only reachable by an explicit
    operator request, the same act `force` exists for on the Run-stage path),
    but it was a bare `record_stage_dispatch` call in a module that does not own
    the counter. It is now a named entry point in the module that does, with the
    policy written down: one and not N, up front, never refused, always charged.

    `test_a_stage_fanout_costs_exactly_one_dispatch` above pins the "one" end to
    end; this pins the "never refused, still charged" end, which that test — run
    against a stage well inside its budget — cannot see.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)

    charge_fanout_dispatch(db_session, ticket.id, "review")

    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET + 1
    assert _override_records(db_session, ticket.id, "review") == []


# -- Review pass 4: the prose fallback, and liveness vs a row status ----------


def test_prose_at_budget_still_cannot_hand_a_stage_a_fresh_counter(db_session: Session):
    """The prose fallback's narrowing — trust the text only when the stage is at
    its budget — was vacuous: being at budget is exactly the state an agent
    wanting a reset is in. An orchestrated agent at 5/5 calls
    `loregarden_block_ticket` with "please review the retry budget"
    (`BLOCK_TICKET` is not denied to orchestrated agents), and the next human
    "Run stage" reset the counter to 1 instead of refusing — skipping both the
    force decision and the `stage_dispatch_override` audit row.

    There is no prose path any more. Only the structural mark counts.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    _blocked_for_another_reason(db_session, ticket, "please review the retry budget")

    assert blocked_on_stage_retry_budget(db_session, ticket, "review") is False

    with pytest.raises(StageRetryBudgetExceeded):
        RunService(db_session).start_stage_execution(ticket, stage_key="review")

    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET
    assert _override_records(db_session, ticket.id, "review") == []


def _strand_a_running_run(
    db_session: Session,
    ticket: Ticket,
    *,
    external_harness: ExternalHarness | None = None,
    age: timedelta = AGENT_RUN_LEASE * 3,
) -> AgentRun:
    """Leave the stage RUNNING behind a run whose supervisor is gone — the state
    a killed server leaves, and the state a runaway leaves behind."""
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, "review", StageStatus.RUNNING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    return make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        stage_key="review",
        status=RunStatus.RUNNING,
        external_harness=external_harness,
        started_at=datetime.now(timezone.utc) - age,
        last_seen_at=datetime.now(timezone.utc) - age,
    )


def test_a_stranded_running_run_does_not_exempt_the_stage_from_the_budget(db_session: Session):
    """`dispatch_pass_open` read `AgentRun.status == RUNNING` and called that a
    live dispatch pass. A run whose supervisor died reads RUNNING forever, so a
    stage it stranded was permanently exempt: `LaneDispatch.dispatch_stage` and
    `OrchestrationCallbackService.start_stage` both dispatch at 5/5 without
    checking or counting, because neither reaps first (`start_run_async` was
    safe only by accident — `fail_interrupted_runs` runs ahead of the guard).

    Liveness, not row status. This pins it for a run kind that HAS a lease
    renewer, which is the only case `agent_run_lease_expired` will judge;
    `test_a_stranded_run_with_no_renewer_does_not_exempt_the_stage_either`
    below covers the kind it will not.
    """
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)
    _strand_a_running_run(db_session, ticket)

    with pytest.raises(ValueError) as refusal:
        _start_stage_via_mcp(db_session, orch_run.id)

    assert str(BUDGET) in str(refusal.value)
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_a_live_running_run_still_groups_its_members_into_one_pass(db_session: Session):
    """The other half: the calling agent's own AgentRun is legitimately RUNNING
    for the stage it is starting, and that is what makes a parallel pass cost
    one attempt instead of three. A renewed lease must still read as live."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)
    run = _strand_a_running_run(db_session, ticket)
    run.last_seen_at = datetime.now(timezone.utc)
    db_session.add(run)
    db_session.commit()

    _start_stage_via_mcp(db_session, orch_run.id)

    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_a_stranded_run_with_no_renewer_does_not_exempt_the_stage_either(
    db_session: Session,
):
    """The same hole one layer down, for the run kind whose liveness nothing
    can disprove.

    `agent_run_lease_expired` returns False for any run kind with no renewer
    (`run_has_renewer`: an externally-harnessed run has none), which is the
    right answer for *reaping* — silence from a thing never asked to speak is
    not evidence, and reaping a live external stage would kill real work. It is
    the wrong answer for grouping: `dispatch_pass_open` asked "is a pass in
    flight?" and got back "not judgeable", which it read as yes. A stranded
    `external_harness` run therefore held `dispatch_pass_open` True forever, and
    every later dispatch of that stage was neither checked nor counted — an
    unbounded, unaudited bypass on the two seams that do not reap first
    (`OrchestrationCallbackService.start_stage`, `LaneDispatch.dispatch_stage`).

    Grouping evidence has to be a run that is LIVE, not merely unjudgeable, so
    the budget puts a staleness ceiling of its own on a no-renewer run rather
    than inheriting `run_lease`'s fail-closed reaping policy.
    """
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)
    _strand_a_running_run(
        db_session,
        ticket,
        external_harness=ExternalHarness.CODEX,
        age=NO_RENEWER_DISPATCH_CEILING * 2,
    )

    with pytest.raises(ValueError) as refusal:
        _start_stage_via_mcp(db_session, orch_run.id)

    assert str(BUDGET) in str(refusal.value)
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


def test_a_recent_run_with_no_renewer_still_groups_its_members_into_one_pass(
    db_session: Session,
):
    """And the regression that ceiling must not cause. An externally-harnessed
    parallel stage dispatches its members through the same per-member loop, all
    within seconds of each other; excluding no-renewer runs from the grouping
    outright would charge such a pass once per member. Inside the ceiling the
    run still reads as live evidence."""
    ticket = _build_ticket(db_session)
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )
    _seed_exhausted_budget(db_session, ticket.id)
    _strand_a_running_run(
        db_session,
        ticket,
        external_harness=ExternalHarness.CODEX,
        age=AGENT_RUN_LEASE * 3,
    )

    _start_stage_via_mcp(db_session, orch_run.id)

    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET


# -- Parking a stage refunds one pass, and the block mark with it -------------


def test_parking_a_stage_clears_the_retry_block_mark_with_the_counter(
    db_session: Session,
):
    """`clear_stage_dispatches` dropped the counter and the reroute ledger but
    left the `stage_retry_block` mark standing, and `stage_parking.park_stage`
    called it. A stage parked after the breaker had blocked it kept a mark whose
    only reader is `blocked_on_stage_retry_budget` — so the *next* block of that
    stage, whoever wrote it and whatever it was for, read as the breaker's own
    and earned an unearned fresh counter. That is the same free reset the prose
    deletion closed, arriving through a stale row instead of a phrase.
    """
    ticket = _build_ticket(db_session)
    _seed_exhausted_budget(db_session, ticket.id)
    record_stage_retry_block(db_session, ticket.id, "review")
    run = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        stage_key="review",
        status=RunStatus.RUNNING,
    )

    park_stage(
        db_session,
        run=run,
        ticket=ticket,
        title="Checkout is on the wrong branch",
        impact="Switch the worktree back and release this stage.",
    )

    # One pass back, not the whole history — see the regression below.
    assert count_stage_dispatches(db_session, ticket.id, "review") == BUDGET - 1
    assert blocked_on_stage_retry_budget(db_session, ticket, "review") is False


def test_a_park_refunds_one_dispatch_and_not_the_whole_counter(db_session: Session):
    """A park must not hand back attempts it never made.

    `park_stage` called `clear_stage_dispatches`, which drops every dispatch
    marker for the stage. So a stage that had genuinely failed four times and
    then hit one environment preflight went back to zero. A stage alternating a
    real attempt with a park would never reach its budget, and the breaker could
    not fire — the same unbounded redispatch this whole module exists to stop,
    arriving through the refund rather than through a missing counter.

    Nothing caught this: the counter really was reset, the ticket really was
    parked, and every assertion about the park passed. It took reading the
    refund against what a refund means.
    """
    ticket = _build_ticket(db_session)
    for _ in range(4):
        record_stage_dispatch(db_session, ticket.id, "review")
    run = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        stage_key="review",
        status=RunStatus.RUNNING,
    )

    park_stage(
        db_session,
        run=run,
        ticket=ticket,
        title="Checkout is on the wrong branch",
        impact="Switch the worktree back and release this stage.",
    )

    # The parked pass's charge, and only it.
    assert count_stage_dispatches(db_session, ticket.id, "review") == 3

    # And the breaker still converges: three more parks do not buy an escape,
    # because each hands back only what its own pass charged.
    for _ in range(3):
        record_stage_dispatch(db_session, ticket.id, "review")
        park_stage(
            db_session,
            run=run,
            ticket=ticket,
            title="Checkout is on the wrong branch",
            impact="Switch the worktree back and release this stage.",
        )
    assert count_stage_dispatches(db_session, ticket.id, "review") == 3
