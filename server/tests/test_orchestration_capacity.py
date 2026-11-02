"""The run-scoped parent host lease: its identity, and when a park gives it back.

Acceptance criteria (lg-machine-resource-1923) R1, R4 and R5.

Two facts are pinned here, and they are the two a refactor drops silently.

**The parent's identity** (R1). A run holds exactly one top-level HOST lease,
`holder_kind=ORCHESTRATION`, `takes_slot=False`, `agent_run_id=None`, sized from
`gates.parent_capacity_footprint` (LIGHT — 0.5 cpu / 512 MB, measured: a SERVICE
parent is 6 x 1.0 + 6 x 0.25 = 7.5 cpu against a 7.0 ceiling at the live lane
count). `takes_slot=False` is load-bearing: the six slots exist to space out
heavy work, and a run's *standing* claim must not crowd out the suites they are
for. It is one line of implementation and nothing else in the system would
report its loss, so it is asserted on its own.

**The park predicate is per park, not per run** (R4/R5). `auto_approve` alone is
the wrong key: `autopilot_sign_off` signs only the legacy sign-off, so an
`auto_approve` run still parks for a person on an authored question, an
authority grant, a recheck, `mark_needs_person` and a block. Keying the hold on
the run's flag would hold a cpu through every one of them — overnight, on a
7-cpu machine, with no dead pid for the reaper to find. One case per row of the
ticket's table, including the `auto_approve=True` run parking on an authored
question, which RELEASES.

The release is recorded as `PARK_RELEASED`, not `RELEASED`: "the holder gave it
back" and "a person now owes this ticket" are different facts, and R12's
"re-acquired after 6m24s" is derived from the difference.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AuthorityRequirement,
    AutopilotAction,
    AutopilotEvent,
    CapacityPool,
    DockerFootprint,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    ExitActionRequirementKind,
    ExitActionResolution,
    InitiativePlan,
    OperatorJudgmentRequirement,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    RuntimeCapabilityRequirement,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowExitAction,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
)
from loregarden.services import orchestration_capacity
from loregarden.services.autopilot_sign_off import (
    LEGACY_SIGN_OFF_KEY,
    GateSigner,
    GateSignerKind,
    signer_for_gate,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.docker_ledger import OCCUPYING, load_pool
from loregarden.services.exit_actions import resolve_exit_actions
from loregarden.services.orchestration_profile import (
    GatesConfig,
    OrchestrationProfile,
    RetryBudgetConfig,
)
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, col, select
from tests.factories import make_ticket, make_workspace
from tests.test_capacity_host_pool import _set_ceiling

#: The measured footprint: LIGHT, 0.5 cpu / 512 MB. Read from the profile below
#: rather than hardcoded at the call sites — retuning is config, not code.
PARENT_CPUS = 0.5
PARENT_MEMORY_MB = 512


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host", autouse=True)
def host_fixture(session):
    """The machine this ticket was measured on: 7.0 cpu / 48332 MB / 6 slots."""
    _set_ceiling(session, CapacityPool.HOST, cpus=7.0, memory_mb=48332, leases=6)


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    workspace = make_workspace(session, slug="parent-capacity")
    workspace.repo_path = str(tmp_path)
    session.add(workspace)
    session.commit()
    return workspace


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(
        session,
        workspace_id=workspace.id,
        external_id="lg-machine-resource-1923",
        state=TicketState.IN_PROGRESS,
    )


def _orch_run(session, ticket, workspace, **fields) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.RUNNING,
        **fields,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _acquire(session, orch_run, ticket, workspace, **kwargs):
    return orchestration_capacity.acquire_or_find_parent(
        session,
        orch_run=orch_run,
        ticket=ticket,
        workspace=workspace,
        footprint=GatesConfig().parent_capacity_footprint,
        **kwargs,
    )


# ---- R1: the parent's identity ------------------------------------------------


def test_the_parent_lease_is_light_slotless_and_keyed_on_the_run(
    session, ticket, workspace
) -> None:
    """R1. Every field here is a decision the ticket records a reason for.

    `takes_slot=False` so a standing claim cannot consume one of the six slots
    that exist to space out heavy work. `agent_run_id=None` so the reaper judges
    it on the orchestration, not on an agent run that has already ended.
    `orchestration_run_id` set so `parent_lease_for` can find it without a new
    `orchestration_runs.capacity_lease_id` column to drift against.
    """
    assert GatesConfig().parent_capacity_footprint is DockerFootprint.LIGHT, (
        "the parent's size is config, not code: a SERVICE parent oversubscribes "
        "this machine at the live lane count (7.5 > 7.0 cpu)"
    )
    orch_run = _orch_run(session, ticket, workspace)

    reservation = _acquire(session, orch_run, ticket, workspace)

    assert reservation.granted
    lease = session.get(DockerLease, reservation.lease_id)
    assert lease.pool is CapacityPool.HOST
    assert lease.holder_kind is DockerHolderKind.ORCHESTRATION
    assert lease.parent_lease_id is None
    assert lease.agent_run_id is None
    assert lease.orchestration_run_id == orch_run.id
    assert lease.ticket_id == ticket.id
    assert lease.workspace_id == workspace.id
    assert lease.holder_pid, "a live orchestrator's pid, so a dead one is reaped at once"
    assert lease.takes_slot is False
    assert (lease.cpus, lease.memory_mb) == (PARENT_CPUS, PARENT_MEMORY_MB)
    assert ticket.external_id in lease.holder_label
    assert orch_run.run_code in lease.holder_label

    found = orchestration_capacity.parent_lease_for(session, orch_run)
    assert found is not None and found.id == lease.id

    assert orchestration_capacity.release_parent(session, orch_run) is True
    assert orchestration_capacity.parent_lease_for(session, orch_run) is None, (
        "a released parent must stop being found, or the next transition reuses a dead grant"
    )
    assert load_pool(session, CapacityPool.HOST).held_cpus == 0


def test_a_second_acquire_in_the_same_run_returns_the_lease_already_held(
    session, ticket, workspace
) -> None:
    """R1. "Exactly one HOST lease keyed on orchestration_run_id" — asking twice
    must hand back the one that exists, never book a second standing claim."""
    orch_run = _orch_run(session, ticket, workspace)

    first = _acquire(session, orch_run, ticket, workspace)
    second = _acquire(session, orch_run, ticket, workspace)

    assert first.granted and second.granted
    assert second.lease_id == first.lease_id
    assert second.reused is True
    assert len(_run_parents(session, orch_run)) == 1
    assert load_pool(session, CapacityPool.HOST).held_cpus == PARENT_CPUS


def _run_parents(session, orch_run) -> list[DockerLease]:
    session.expire_all()
    return list(
        session.exec(
            select(DockerLease).where(
                DockerLease.orchestration_run_id == orch_run.id,
                col(DockerLease.parent_lease_id).is_(None),
                DockerLease.pool == CapacityPool.HOST,
                col(DockerLease.status).in_(OCCUPYING),
            )
        ).all()
    )


# ---- R4: the park predicate, one case per row of the table --------------------


def _gate_payload(*actions: WorkflowExitAction) -> str:
    """A gate's exit-action payload, written by the real resolver.

    Built through `resolve_exit_actions` rather than by hand so these cases are
    gates the server would actually accept: a payload assembled by the test
    could claim a shape the resolver never produces, and the predicate would be
    pinned against fiction.
    """
    stage = WorkflowStageDef(
        key="spec", name="Spec", exit_actions_enabled=True, exit_actions=list(actions)
    )
    resolution = resolve_exit_actions(stage, None)
    return ExitActionResolution(
        human_required_actions=resolution.human_required_actions,
        allowed_actions=resolution.allowed_actions,
    ).model_dump_json()


def _judgment(key: str, label: str) -> WorkflowExitAction:
    return WorkflowExitAction(
        key=key,
        label=label,
        requirement=OperatorJudgmentRequirement(
            kind=ExitActionRequirementKind.OPERATOR_JUDGMENT, decision_prompt=f"{label}?"
        ),
    )


LEGACY_SIGN_OFF = _judgment(LEGACY_SIGN_OFF_KEY, "Approve Spec completion")
#: An operator-judgment gate a person authored as a real question. Judgment-only,
#: so the generic `gate_is_operator_judgment_only` check passes it — and the
#: predicate must still refuse it, which is why this case exists at all.
AUTHORED_QUESTION = _judgment("ship-to-users", "Ship this to users")
AUTHORITY_GRANT = WorkflowExitAction(
    key="grant-force-push",
    label="Grant force-push",
    requirement=AuthorityRequirement(
        kind=ExitActionRequirementKind.AUTHORITY, authority_scope="git.force_push"
    ),
)
#: A requirement cleared by re-checking the world, not by anyone deciding.
RECHECK = WorkflowExitAction(
    key="ci-green",
    label="CI is green",
    requirement=RuntimeCapabilityRequirement(
        kind=ExitActionRequirementKind.RUNTIME_CAPABILITY, capability_id="ci.status"
    ),
)


def _gate(session, ticket, *actions: WorkflowExitAction) -> Approval:
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        status=ApprovalStatus.PENDING,
        stage_key="spec",
        tool_input_json=_gate_payload(*actions),
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval


def _autopilot_dispatched(session, ticket) -> InitiativePlan:
    initiative = Ticket(
        external_id=f"init-{uuid4()}",
        title="Machine capacity",
        work_item_type=WorkItemType.INITIATIVE,
    )
    session.add(initiative)
    session.commit()
    plan = InitiativePlan(initiative_id=initiative.id, autopilot=True)
    session.add_all(
        [
            plan,
            AutopilotEvent(
                initiative_id=initiative.id,
                action=AutopilotAction.DISPATCHED,
                ticket_id=ticket.id,
            ),
        ]
    )
    session.commit()
    return plan


#: One row per row of the ticket's table. `park` names the row; `hold` is the
#: lease's fate. The two HOLD rows are the only ones where automation owns the
#: approval; everything else is a person's, and a person has no dead pid.
PARK_CASES = [
    pytest.param(
        [LEGACY_SIGN_OFF], True, False, GateSignerKind.AUTO_APPROVE_RUN, id="auto-approve-signs-it"
    ),
    pytest.param([LEGACY_SIGN_OFF], False, True, GateSignerKind.AUTOPILOT, id="autopilot-signs-it"),
    pytest.param([AUTHORED_QUESTION], True, False, None, id="auto-approve-run-authored-question"),
    pytest.param([AUTHORED_QUESTION], False, True, None, id="autopilot-authored-question"),
    pytest.param([AUTHORITY_GRANT], True, True, None, id="authority-grant"),
    pytest.param([RECHECK], True, True, None, id="recheck"),
    pytest.param([LEGACY_SIGN_OFF, AUTHORED_QUESTION], True, True, None, id="mixed-gate"),
    pytest.param([LEGACY_SIGN_OFF], False, False, None, id="both-automations-off"),
]


@pytest.mark.parametrize(("actions", "auto_approve", "autopilot", "expected"), PARK_CASES)
def test_release_for_park_decides_per_park_not_per_run(
    session, ticket, workspace, actions, auto_approve, autopilot, expected
) -> None:
    """R4. The predicate is the approval's, never the run's flag.

    `auto-approve-run-authored-question` is the case that makes the distinction
    load-bearing: the run carries `auto_approve=True`, nothing in automation
    will answer the question, and the parent must be given back.
    """
    orch_run = _orch_run(session, ticket, workspace, auto_approve=auto_approve)
    if autopilot:
        _autopilot_dispatched(session, ticket)
    approval = _gate(session, ticket, *actions)
    reservation = _acquire(session, orch_run, ticket, workspace)
    assert reservation.granted

    signer = signer_for_gate(session, ticket, orch_run, approval)
    released = orchestration_capacity.release_for_park(session, orch_run, ticket, approval)

    if expected is None:
        assert signer is None
        assert released is True
        lease = session.get(DockerLease, reservation.lease_id)
        assert lease.status is DockerLeaseStatus.RELEASED
        assert lease.end_reason is DockerLeaseEndReason.PARK_RELEASED, (
            "a park a person owes is not the holder handing a lease back; the "
            "re-acquire figure on the ticket is derived from the difference"
        )
        assert load_pool(session, CapacityPool.HOST).held_cpus == 0
    else:
        assert signer is not None
        assert signer.kind is expected
        assert released is False
        assert session.get(DockerLease, reservation.lease_id).status in OCCUPYING


def test_a_park_with_no_approval_at_all_releases_the_parent(session, ticket, workspace) -> None:
    """R5. `loregarden_mark_needs_person` and a block park with no gate row.

    There is nothing for any signer to own, so these are release cases — and
    they are the ones a predicate written around approvals forgets.
    """
    orch_run = _orch_run(session, ticket, workspace, auto_approve=True)
    reservation = _acquire(session, orch_run, ticket, workspace)

    assert orchestration_capacity.release_for_park(session, orch_run, ticket, None) is True
    lease = session.get(DockerLease, reservation.lease_id)
    assert lease.end_reason is DockerLeaseEndReason.PARK_RELEASED


def test_release_for_park_never_reads_the_runs_auto_approve_flag(
    session, ticket, workspace
) -> None:
    """R4, as a structural claim rather than a behavioural one.

    Every behavioural case above can be passed by code that reads
    `orch_run.auto_approve` and then happens to agree. This asserts the
    composite is the only thing consulted: with the flag inverted under the
    predicate's feet, the answer must not change.
    """
    orch_run = _orch_run(session, ticket, workspace, auto_approve=True)
    approval = _gate(session, ticket, AUTHORED_QUESTION)
    _acquire(session, orch_run, ticket, workspace)
    assert signer_for_gate(session, ticket, orch_run, approval) is None

    orch_run.auto_approve = False
    session.add(orch_run)
    session.commit()

    assert signer_for_gate(session, ticket, orch_run, approval) is None
    assert orchestration_capacity.release_for_park(session, orch_run, ticket, approval) is True


def test_the_two_sign_off_call_sites_use_the_same_composite(session, ticket, workspace) -> None:
    """R4. `signer_for_gate` removes a duplication rather than adding one.

    `run_completion._sign_off_gate_if_permitted` and
    `subtree_auto_run.resolve_gate_if_permitted` each hand-computed
    autopilot-or-auto-approve. Two copies of a predicate drift, and the drift
    would be invisible: one site would sign a gate the other would park on.
    """
    import inspect

    from loregarden.services import run_completion, subtree_auto_run

    for module in (run_completion, subtree_auto_run):
        source = inspect.getsource(module)
        assert "signer_for_gate" in source, f"{module.__name__} recomputes the signer"
        assert "auto_approve_may_sign_off" not in source, (
            f"{module.__name__} still hand-computes what signer_for_gate decides"
        )


def test_a_signer_names_the_plan_that_signs_it(session, ticket, workspace) -> None:
    """R4. The autopilot signer carries its plan, because the sign-off record
    names the initiative — `record_autopilot_sign_off` cannot write the
    `AutopilotEvent` without it."""
    orch_run = _orch_run(session, ticket, workspace, auto_approve=False)
    plan = _autopilot_dispatched(session, ticket)
    approval = _gate(session, ticket, LEGACY_SIGN_OFF)

    signer = signer_for_gate(session, ticket, orch_run, approval)

    assert signer == GateSigner(kind=GateSignerKind.AUTOPILOT, plan=plan)


# ---- R5: the re-acquire, and the finally that guarantees it --------------------


def test_an_unresolvable_park_releases_the_parent_and_the_next_gate_re_acquires_it(
    session, ticket, workspace
) -> None:
    """R5. A park a person owes gives the capacity back; resolving it pays the
    line again, and that second trip is visible as a lease of its own.

    The resume is a *new* orchestration run, because `_pause_orchestration`
    completes the run SUCCEEDED. That is why the parent is keyed on the run
    rather than the ticket: a ticket-keyed parent surviving the park would make
    this criterion unimplementable.
    """
    first_run = _orch_run(session, ticket, workspace, auto_approve=True)
    approval = _gate(session, ticket, AUTHORED_QUESTION)
    first = _acquire(session, first_run, ticket, workspace)
    assert first.granted and not first.reused

    assert orchestration_capacity.release_for_park(session, first_run, ticket, approval) is True
    first_run.status = OrchestrationRunStatus.SUCCEEDED
    session.add(first_run)
    session.commit()

    # The approval resolves and the ticket is picked up again — a new run.
    approval.status = ApprovalStatus.APPROVED
    session.add(approval)
    session.commit()
    second_run = _orch_run(session, ticket, workspace, auto_approve=True)
    second = _acquire(session, second_run, ticket, workspace)

    assert second.granted
    assert second.lease_id != first.lease_id, "the resumed run pays its own trip to the line"
    assert second.reused is False
    ended = session.get(DockerLease, first.lease_id)
    assert ended.end_reason is DockerLeaseEndReason.PARK_RELEASED
    assert orchestration_capacity.parent_lease_for(session, second_run).id == second.lease_id
    # One standing claim on the machine, not two: the first was given back.
    assert load_pool(session, CapacityPool.HOST).held_cpus == PARENT_CPUS


# `execute()` has many exits and one `finally`. Each id below is a different
# return path out of the loop; the parent must be gone at every one of them.
# The predicate decides which parks hold; the finally guarantees the paths no
# predicate enumerates — a retry-budget block, a stage limit, a dependency hold,
# an exception at the run boundary.
EXECUTE_EXITS = [
    pytest.param("stage_limit", id="paused-at-its-stage-limit"),
    pytest.param("budget_block", id="blocked-by-the-retry-budget"),
    pytest.param("nothing_to_run", id="nothing-left-to-run"),
    pytest.param("boundary_exception", id="an-exception-at-the-run-boundary"),
    pytest.param("completed", id="the-run-completed"),
]


@pytest.fixture(name="runnable")
def runnable_fixture(session, workspace):
    """A ticket with a one-agent workflow, its template and instance.

    `repo_path` points at nothing that exists, so a dispatch never tries to
    `git checkout` a branch — the same trick `test_stage_retry_budget_orchestrator`
    uses, and for the same reason.
    """
    workspace.repo_path = "/nonexistent/parent-capacity-repo"
    session.add(workspace)
    stages = [
        WorkflowStageDef(
            key="implement", name="Implement", order=1, agent_id="backend_implementer"
        ),
        WorkflowStageDef(key="done", name="Done", order=2, terminal=True),
    ]
    template = WorkflowTemplate(
        slug=f"parent-capacity-{uuid4()}",
        name="Parent capacity",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps([{"from": "implement", "to": "done", "when": "pass"}]),
    )
    session.add(template)
    session.commit()
    ticket = Ticket(
        external_id=f"pc-{uuid4()}",
        workspace_id=workspace.id,
        title="Parent capacity",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.PENDING,
    )
    session.add(ticket)
    session.commit()
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(stages),
        )
    )
    session.commit()
    session.refresh(ticket)
    return ticket


def _held_parents(session) -> list[DockerLease]:
    session.expire_all()
    return [
        lease
        for lease in session.exec(select(DockerLease))
        if lease.holder_kind is DockerHolderKind.ORCHESTRATION
        and lease.parent_lease_id is None
        and lease.status in OCCUPYING
    ]


@pytest.mark.parametrize("exit_path", EXECUTE_EXITS)
def test_no_park_leaves_a_held_parent_after_execute_returns(
    session, workspace, runnable, monkeypatch, exit_path
) -> None:
    """R5, and the whole safety argument for holding a lease across stages.

    A parent held past `execute()` is a cpu nobody is using on a 7-cpu machine,
    and the orchestrator's thread is gone — so no `finally` of anyone else's
    runs, and for a park that waits on a person there is no dead pid for the
    reaper to judge. The parent is acquired here through the real
    `acquire_or_find_parent`, at the moment the run row appears, so this test
    exercises the release rather than a stand-in for it.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor

    orchestrator = BuiltinOrchestrator(session)
    started = orchestrator.callbacks.start_orchestration_run

    def start_and_claim(ticket, **kwargs):
        orch_run = started(ticket, **kwargs)
        # Lazily, on the first transition — simulated here as "as soon as the
        # run exists", because several of these exits never reach a gate.
        claim = _acquire(session, orch_run, ticket, workspace)
        assert claim.granted, "the parent must be held before the exit under test"
        return orch_run

    monkeypatch.setattr(orchestrator.callbacks, "start_orchestration_run", start_and_claim)

    def succeed(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED
        run.stdout = (
            "ok\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
            + json.dumps({"status": "pass", "confidence": 0.9})
            + "\n<<<END_STAGE_REPORT>>>\n"
        )
        self.session.add(run)
        self.session.commit()
        return run

    def explode(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        raise RuntimeError("the orchestrator itself stopped")

    profile = OrchestrationProfile(
        slug="parent-capacity",
        gates=GatesConfig(enabled=False),
        retry_budget=RetryBudgetConfig(
            enabled=exit_path == "budget_block", max_attempts_per_stage=1
        ),
    )
    if exit_path == "budget_block":
        from loregarden.services.stage_retry_budget import record_stage_dispatch

        record_stage_dispatch(session, runnable.id, "implement")
    if exit_path == "nothing_to_run":
        runnable.state = TicketState.DONE
        session.add(runnable)
        session.commit()
    monkeypatch.setattr(
        CliAgentExecutor, "execute", explode if exit_path == "boundary_exception" else succeed
    )

    # max_stages=1 pauses at the limit check on the loop after `implement`;
    # 0 means "no limit" to execute(), not "stop immediately".
    orchestrator.execute(runnable, profile, max_stages=1 if exit_path == "stage_limit" else 10)

    assert _held_parents(session) == [], (
        f"{exit_path} returned from execute() with a parent lease still held"
    )
    assert load_pool(session, CapacityPool.HOST).held_cpus == 0
