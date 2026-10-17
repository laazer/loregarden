"""The edges of retrying a landing after a resolution turn (929).

`test_landing_conflict_resolver.py` proves the happy path: a resolver that
really resolves must land in the same orchestration. Retrying a landing because
the terminal stage is sitting PENDING is a widening of
`BuiltinOrchestrator._stage_wants_another_attempt`, and the dangerous widening
is the one that reads *any* terminal stage left PENDING as another attempt.
`stage_transient_retry.stage_rearmed_for_latest_run`'s docstring already names
what that would break — a cancelled run leaves a stage PENDING too, and
re-dispatching there restarts work a human stopped.

So these are the cases where the retry must *not* happen, or must stop:

- a resolver that reports `pass` and resolves nothing (what every other
  orchestrator test in the sibling module fakes) must spend its bounded turns
  and then block for a person, not spin and not close the run `succeeded` over
  unlanded work — which is the second half of 782's defect report;
- a resolver that resolves the conflict and nonetheless reports `blocked` must
  still block: the landing must not be retried behind the back of an agent that
  said a person is needed;
- an ordinary stage sitting PENDING, and a terminal stage left PENDING by a
  *cancelled* run, are not another attempt;
- a terminal stage that carries an agent of its own is PENDING after every
  pass it makes, so reading that as a landing owed re-dispatches it until
  `max_stages` with nothing landed.

The fixtures come from the sibling module rather than a second copy: the
conflict has to be a real git conflict for any of this to mean anything.
"""

from __future__ import annotations

import json
from uuid import uuid4

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    AgentRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.workflow_state import (
    initial_stages_json,
    parse_stage_map,
    set_stage_status,
)
from tests.factories import make_agent_run
from tests.test_landing_conflict_resolver import (  # noqa: F401 - imported as pytest fixtures
    _land,
    _resolve_by_hand,
    conflicted_fixture,
    milestone_fixture,
    repo_fixture,
    session_fixture,
    workspace_fixture,
)
from tests.worktree_helpers import blocking_text, git, make_repo

_PASS_REPORT = (
    "<<<LOREGARDEN_STAGE_REPORT>>>\n"
    + json.dumps({"status": "pass", "confidence": 0.9})
    + "\n<<<END_STAGE_REPORT>>>\n"
)
_BLOCKED_REPORT = (
    "<<<LOREGARDEN_STAGE_REPORT>>>\n"
    + json.dumps(
        {
            "status": "blocked",
            "confidence": 0.9,
            "reroute_context": "both sides want the same line; a person has to choose",
            "blocked_kind": "decision",
        }
    )
    + "\n<<<END_STAGE_REPORT>>>\n"
)


def _cap_attempts(session, ticket: Ticket, attempts: int) -> None:
    """One resolution turn allowed means one, so the bound is observable."""
    ticket.git_automation_json = json.dumps(
        {"auto_resolve_conflicts": True, "max_conflict_resolve_attempts": attempts}
    )
    session.add(ticket)
    session.commit()


def _fake_resolver(dispatched: list[tuple[str, str]], *, stdout: str, resolve=None):
    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        dispatched.append((run.agent_id, run.stage_key))
        if resolve is not None:
            resolve(worker_ticket)
        return self.orchestration.complete_run(
            run, status=RunStatus.SUCCEEDED, stdout=stdout, stderr=""
        )

    return fake_execute


def _stages(session, ticket):
    return OrchestrationService(session)._resolve_stages(ticket)


def _profile() -> OrchestrationProfile:
    return OrchestrationProfile(slug="resolver", gates=GatesConfig(enabled=False))


def test_a_resolver_that_reports_pass_without_resolving_blocks_instead_of_succeeding(
    session, conflicted, monkeypatch
):
    """The retry is bounded, and a spent bound is a person's, not a `succeeded` run.

    A resolver's turn can report `pass` having fixed nothing — 782's did, after
    calling the landing tool it is refused. With the landing retried, the retry
    conflicts again, and `max_conflict_resolve_attempts` is what stops the loop
    there. The run must not end `succeeded` with the work unlanded.
    """
    _cap_attempts(session, conflicted, 1)
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        CliAgentExecutor, "execute", _fake_resolver(dispatched, stdout=_PASS_REPORT)
    )

    orch_run = BuiltinOrchestrator(session).execute(conflicted, _profile(), max_stages=5)

    session.refresh(conflicted)
    assert dispatched == [(REPAIR_AGENT_ID, "done")], (
        "one turn was allowed; the loop must not keep re-dispatching the resolver"
    )
    assert conflicted.landed_sha == "", "nothing was resolved, so nothing may have landed"
    assert conflicted.state is TicketState.BLOCKED
    assert orch_run.status is OrchestrationRunStatus.BLOCKED, (
        "a run that leaves its ticket unlanded must not report success (929)"
    )
    assert "shared.txt" in blocking_text(session, conflicted), (
        "the person who picks it up is told which file is still conflicted"
    )


def test_a_resolver_that_resolves_but_reports_blocked_still_blocks(
    session, conflicted, workspace, repo, monkeypatch
):
    """A resolution the agent itself would not sign off on is not a landing.

    This is the mutation that separates "a *passing* run left the terminal stage
    PENDING" from "the terminal stage is PENDING": the second one retries the
    landing here too, lands a resolution the resolver reported `blocked` on, and
    finishes the ticket DONE while saying a person was needed.
    """
    target = resolve_target_branch(session, conflicted, workspace, repo_root=repo)
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        CliAgentExecutor,
        "execute",
        _fake_resolver(
            dispatched,
            stdout=_BLOCKED_REPORT,
            resolve=lambda ticket: _resolve_by_hand(repo, ticket, target),
        ),
    )

    orch_run = BuiltinOrchestrator(session).execute(conflicted, _profile(), max_stages=5)

    session.refresh(conflicted)
    assert dispatched == [(REPAIR_AGENT_ID, "done")]
    assert git(repo, "merge-base", "--is-ancestor", target, conflicted.branch).returncode == 0, (
        "the fake really did merge, or this proves nothing about ignoring its report"
    )
    assert conflicted.landed_sha == "", "the resolver asked for a person; nothing lands"
    assert conflicted.state is TicketState.BLOCKED
    assert conflicted.state is not TicketState.DONE
    assert orch_run.status is OrchestrationRunStatus.BLOCKED


def test_an_ordinary_pending_stage_is_not_another_attempt(session, conflicted):
    """R4: the widening must not make every PENDING stage re-dispatch forever."""
    conflicted.workflow_stage_key = "work"
    conflicted.workflow_stage_status = StageStatus.PENDING
    conflicted.next_agent = ""
    conflicted.scope_reroute_agent = ""
    session.add(conflicted)
    session.commit()

    orchestrator = BuiltinOrchestrator(session)

    assert orchestrator._stage_wants_another_attempt(conflicted, "work") is False


def test_a_terminal_stage_left_pending_by_a_cancelled_run_is_not_another_attempt(
    session, conflicted
):
    """The exclusion `stage_rearmed_for_latest_run` names: a cancelled run leaves
    the stage PENDING too, and re-dispatching there restarts work a human stopped."""
    _land(session, conflicted)
    session.refresh(conflicted)
    assert parse_stage_map(*_stages(session, conflicted))["done"] is StageStatus.PENDING, (
        "the fixture must leave the terminal stage PENDING, or this tests nothing"
    )
    assert conflicted.workflow_stage_status is StageStatus.PENDING, (
        "and the ticket row must agree, which is the state the predicate reads"
    )
    make_agent_run(
        session,
        workspace_id=conflicted.workspace_id,
        ticket_id=conflicted.id,
        run_code="run-resolver-cancelled",
        stage_key="done",
        agent_id=REPAIR_AGENT_ID,
        status=RunStatus.CANCELLED,
    )
    conflicted.next_agent = ""
    conflicted.scope_reroute_agent = ""
    session.add(conflicted)
    session.commit()

    orchestrator = BuiltinOrchestrator(session)

    assert orchestrator._stage_wants_another_attempt(conflicted, "done") is False


def test_a_terminal_stage_that_carries_its_own_agent_does_not_redispatch_forever(
    session, tmp_path, monkeypatch
):
    """The widening this clause must not have.

    "Terminal, current, PENDING, and the latest run SUCCEEDED" is also true on
    every pass of a terminal stage that names an agent: dispatch, pass,
    `run_completion` leaves it PENDING, the predicate says another attempt,
    dispatch again — to `max_stages`, with nothing landed. No live template
    authors one, but `studio_workflow_validation.validate_has_terminal_stage`
    never requires the terminal stage to be agentless, so Studio can. The
    landing is owed by an *agentless* terminal marker, and only that.
    """
    repo = make_repo(tmp_path)
    workspace = Workspace(slug="terminal-agent", name="terminal-agent", repo_path=str(repo))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    ticket = Ticket(
        external_id="lg-terminal-agent-1",
        workspace_id=workspace.id,
        title="A terminal stage with an agent of its own",
        branch="loregarden/lg-terminal-agent-1",
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)

    stages = [
        WorkflowStageDef(key="work", name="Work", order=1, agent_id="backend_implementer"),
        WorkflowStageDef(
            key="signoff",
            name="Signoff",
            order=2,
            terminal=True,
            stage_type="agent",
            agent_id="architecture_reviewer",
        ),
    ]
    template = WorkflowTemplate(
        slug=f"terminal-agent-{uuid4()}",
        name="terminal-agent",
        stages_json=json.dumps([stage.model_dump(mode="json") for stage in stages]),
        transitions_json=json.dumps([{"from": "work", "to": "signoff", "when": "pass"}]),
    )
    session.add(template)
    session.commit()
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="signoff",
        stages_json=initial_stages_json(stages),
    )
    session.add(instance)
    session.commit()
    set_stage_status(ticket, instance, stages, "work", StageStatus.DONE)
    ticket.workflow_stage_key = "signoff"
    ticket.workflow_stage_status = StageStatus.PENDING
    session.add(ticket)
    session.add(instance)
    session.commit()
    session.refresh(ticket)

    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        CliAgentExecutor, "execute", _fake_resolver(dispatched, stdout=_PASS_REPORT)
    )

    BuiltinOrchestrator(session).execute(ticket, _profile(), max_stages=4)

    assert dispatched == [("architecture_reviewer", "signoff")], (
        "a terminal stage with its own agent gets one pass, not one per max_stages"
    )
