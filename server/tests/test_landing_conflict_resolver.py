"""A landing conflict arms a resolution turn instead of blocking (801).

`landing.py` blocked every conflict and said so in its own docstring: the
resolver "has to be dispatched on the implement stage, not the terminal one,
and that routing is not designed". The reason it could not go on the terminal
stage was real — a passing run marks its stage DONE, and `set_stage_status` on
the terminal stage derives `done` on the ticket, which would finish it with
the work unlanded (777's failure, from a new direction). So the run completion
leaves a terminal stage PENDING, and the resolver can be dispatched where the
ticket already is.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.landing import land_before_done
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session
from tests.history_helpers import decision_kinds
from tests.worktree_helpers import at_terminal_stage, blocking_text, commit_on, git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="resolver", name="resolver", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    ms = Ticket(
        external_id="lg-ms-801r",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


@pytest.fixture(name="conflicted")
def conflicted_fixture(session, workspace, repo, milestone):
    """A ticket at its terminal stage whose branch conflicts with its target.

    Built in a fixture so a broken setup is an ERROR, not a green assertion:
    every test here depends on the conflict being real.
    """
    ticket = Ticket(
        external_id="lg-conflict-1",
        workspace_id=workspace.id,
        title="Conflicts on landing",
        branch="loregarden/lg-conflict-1",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
        git_automation_json=json.dumps({"auto_resolve_conflicts": True}),
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "shared.txt", "ticket\n")
    commit_on(repo, target, "shared.txt", "sibling\n")
    at_terminal_stage(session, ticket)
    return ticket


def _land(session, ticket):
    orch = OrchestrationService(session)
    instance, stages = orch._resolve_stages(ticket)
    return (
        land_before_done(session, ticket, instance, stages, stage_key=ticket.workflow_stage_key),
        instance,
        stages,
    )


def test_a_conflict_arms_the_resolver_instead_of_blocking(session, conflicted):
    derived, instance, stages = _land(session, conflicted)

    assert derived is False, "the ticket must not reach done with its work unlanded"
    session.refresh(conflicted)
    assert conflicted.state != TicketState.BLOCKED, "a ticket with a resolver coming is not blocked"
    assert conflicted.next_agent == REPAIR_AGENT_ID
    assert parse_stage_map(instance, stages)["done"] is StageStatus.PENDING
    assert "shared.txt" in conflicted.blocking_issues, "the brief names the conflicted file"

    kinds = decision_kinds(session, conflicted.id)
    assert "dispatched_landing_resolver" in kinds


def test_a_ticket_with_no_override_gets_the_resolver_by_default(session, conflicted):
    """Sibling tickets on one integration branch conflict as a matter of course;
    blocking each for a person stalled whole trees (lg-durable-remote-336)."""
    conflicted.git_automation_json = ""
    session.add(conflicted)
    session.commit()

    _land(session, conflicted)

    session.refresh(conflicted)
    assert conflicted.state != TicketState.BLOCKED
    assert conflicted.next_agent == REPAIR_AGENT_ID


def test_the_flag_off_still_blocks_for_a_person(session, conflicted):
    conflicted.git_automation_json = json.dumps({"auto_resolve_conflicts": False})
    session.add(conflicted)
    session.commit()

    derived, instance, stages = _land(session, conflicted)

    assert derived is False
    session.refresh(conflicted)
    assert conflicted.state == TicketState.BLOCKED
    assert parse_stage_map(instance, stages)["done"] is StageStatus.BLOCKED
    assert "shared.txt" in blocking_text(session, conflicted)


def test_the_attempt_cap_is_honoured_and_then_it_blocks(session, conflicted):
    """`max_conflict_resolve_attempts` is set in the git automation panel and
    governed nothing before this. One attempt allowed means one, then a person."""
    conflicted.git_automation_json = json.dumps(
        {"auto_resolve_conflicts": True, "max_conflict_resolve_attempts": 1}
    )
    session.add(conflicted)
    session.commit()

    _land(session, conflicted)
    session.refresh(conflicted)
    assert conflicted.next_agent == REPAIR_AGENT_ID, "first attempt armed"

    # The turn ran and did not fix it: a resolver run exists on the stage.
    from tests.factories import make_agent_run

    make_agent_run(
        session,
        workspace_id=conflicted.workspace_id,
        ticket_id=conflicted.id,
        run_code="run-resolver-1",
        stage_key="done",
        agent_id=REPAIR_AGENT_ID,
    )
    conflicted.next_agent = ""
    session.add(conflicted)
    session.commit()

    derived, instance, stages = _land(session, conflicted)

    assert derived is False
    session.refresh(conflicted)
    assert conflicted.state == TicketState.BLOCKED, "the cap is spent; a person reads it"
    assert parse_stage_map(instance, stages)["done"] is StageStatus.BLOCKED
    assert conflicted.next_agent != REPAIR_AGENT_ID


def test_a_clean_landing_never_arms_a_resolver(session, workspace, repo, milestone):
    ticket = Ticket(
        external_id="lg-conflict-2",
        workspace_id=workspace.id,
        title="Lands cleanly",
        branch="loregarden/lg-conflict-2",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
        git_automation_json=json.dumps({"auto_resolve_conflicts": True}),
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "own.txt", "work\n")
    at_terminal_stage(session, ticket)

    derived, _, _ = _land(session, ticket)

    assert derived is True
    session.refresh(ticket)
    assert ticket.next_agent != REPAIR_AGENT_ID
    assert ticket.landed_sha


def test_a_passing_run_never_marks_the_terminal_stage_done(session, conflicted):
    """The hazard that kept the resolver off the terminal stage: a passing run
    marks its stage DONE, and `set_stage_status` on the terminal stage derives
    `done` on the ticket — finishing it with the work unlanded."""
    from loregarden.models.domain import RunStatus
    from loregarden.services.run_completion import _advance_clean_exit
    from loregarden.services.stage_report import parse_stage_report
    from tests.factories import make_agent_run

    run = make_agent_run(
        session,
        workspace_id=conflicted.workspace_id,
        ticket_id=conflicted.id,
        run_code="run-resolver-pass",
        stage_key="done",
        agent_id=REPAIR_AGENT_ID,
        status=RunStatus.SUCCEEDED,
    )
    orch = OrchestrationService(session)
    instance, stages = orch._resolve_stages(conflicted)
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        + json.dumps({"status": "pass", "summary": "resolved the conflict"})
        + "\n<<<END_STAGE_REPORT>>>\n"
    )
    assert report is not None, "the fixture's report must parse, or this proves nothing"

    _advance_clean_exit(orch, conflicted, run, report, instance, stages)

    assert parse_stage_map(instance, stages)["done"] is StageStatus.PENDING
    session.refresh(conflicted)
    assert conflicted.state != TicketState.DONE, "landing, not the run, finishes the ticket"


def test_the_loop_dispatches_the_pinned_resolver_instead_of_finalizing(
    session, conflicted, monkeypatch
):
    """The terminal stage names no agent, so the loop read it as agentless and
    finalized — which is why the resolver could not be dispatched where the
    ticket already was. A pin outranks the stage's own emptiness."""
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.models.domain import AgentRun, RunStatus
    from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
    from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile

    _land(session, conflicted)
    session.refresh(conflicted)
    assert conflicted.next_agent == REPAIR_AGENT_ID, "the fixture must arm the pin"

    dispatched: list[tuple[str, str]] = []

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        dispatched.append((run.agent_id, run.stage_key))
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=(
                "<<<LOREGARDEN_STAGE_REPORT>>>\n"
                + json.dumps({"status": "pass", "confidence": 0.9})
                + "\n<<<END_STAGE_REPORT>>>\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)
    profile = OrchestrationProfile(slug="resolver", gates=GatesConfig(enabled=False))

    BuiltinOrchestrator(session).execute(conflicted, profile, max_stages=1)

    assert dispatched == [(REPAIR_AGENT_ID, "done")], (
        "the resolver runs on the terminal stage; the loop must not finalize past the pin"
    )


def test_the_dispatched_resolver_is_told_what_conflicted(session, conflicted, monkeypatch):
    """The brief has to reach the agent, not just the ticket row.

    It was carried only in `ticket.blocking_issues`, which `_prepare_stage_start`
    blanks the moment the stage is dispatched — before the executor renders the
    prompt. 782's resolver therefore read "no blocking_issues", never learned
    there was a conflict, and spent its one turn calling the landing tool it is
    refused (lg-milestone-that-929).
    """
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.agents.stage_context import build_orchestration_context
    from loregarden.models.domain import AgentRun, RunStatus
    from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
    from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile

    _land(session, conflicted)
    rendered: list[str] = []

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        rendered.append(
            build_orchestration_context(
                ticket=worker_ticket, run=run, stage_def=None, session=self.session
            )
        )
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=(
                "<<<LOREGARDEN_STAGE_REPORT>>>\n"
                + json.dumps({"status": "pass", "confidence": 0.9})
                + "\n<<<END_STAGE_REPORT>>>\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)
    profile = OrchestrationProfile(slug="resolver", gates=GatesConfig(enabled=False))

    BuiltinOrchestrator(session).execute(conflicted, profile, max_stages=1)

    assert len(rendered) == 1, "the resolver must have been dispatched once"
    assert "shared.txt" in rendered[0], "the resolver's prompt names the conflicted file"
    assert conflicted.branch in rendered[0], "and the branch it has to resolve on"


def test_a_resolver_that_blocks_ends_the_run_blocked(session, conflicted, monkeypatch):
    """782's resolver blocked for a person, and its orchestration still completed
    `succeeded` over a blocked ticket (lg-milestone-that-929)."""
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.models.domain import AgentRun, OrchestrationRunStatus, RunStatus
    from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
    from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile

    _land(session, conflicted)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=(
                "<<<LOREGARDEN_STAGE_REPORT>>>\n"
                + json.dumps(
                    {
                        "status": "blocked",
                        "confidence": 0.9,
                        "reroute_context": "needs a person",
                        "blocked_kind": "human_action",
                    }
                )
                + "\n<<<END_STAGE_REPORT>>>\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)
    profile = OrchestrationProfile(slug="resolver", gates=GatesConfig(enabled=False))

    orch_run = BuiltinOrchestrator(session).execute(conflicted, profile, max_stages=5)

    session.refresh(conflicted)
    assert conflicted.state is TicketState.BLOCKED
    assert orch_run.status is OrchestrationRunStatus.BLOCKED


def _resolve_by_hand(repo, ticket, target):
    """Merge the target into the ticket branch and keep both sides, as a person
    did for 782 (41f216f9)."""
    tree = repo.parent / "hand-resolve"
    git(repo, "worktree", "add", "-q", str(tree), ticket.branch)
    merged = subprocess.run(
        ["git", "merge", "-q", "--no-edit", target], cwd=tree, capture_output=True, text=True
    )
    assert merged.returncode != 0, "the fixture's conflict must be real"
    (tree / "shared.txt").write_text("sibling\nticket\n")
    git(tree, "add", "shared.txt")
    git(tree, "commit", "-q", "--no-edit")
    git(repo, "worktree", "remove", "--force", str(tree))


def test_landing_by_hand_finishes_a_ticket_blocked_on_its_landing(
    session, conflicted, workspace, repo
):
    """A person resolves the conflict and lands it; the ticket finishes.

    782 was landed by hand at 15:16 and its `landed_sha` recorded, but the
    terminal stage stayed BLOCKED until someone requeued it and started an
    orchestration that had nothing to do but notice (lg-milestone-that-928).
    """
    from loregarden.mcp.ticket_ops_tools import execute_ticket_ops_tool
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

    conflicted.git_automation_json = json.dumps({"auto_resolve_conflicts": False})
    session.add(conflicted)
    session.commit()
    _land(session, conflicted)
    session.refresh(conflicted)
    assert conflicted.state is TicketState.BLOCKED, "the fixture must block on the landing"

    target = resolve_target_branch(session, conflicted, workspace, repo_root=repo)
    _resolve_by_hand(repo, conflicted, target)

    payload = json.loads(
        execute_ticket_ops_tool(
            "loregarden_land_ticket",
            session,
            OrchestrationCallbackService(session),
            {"ticket_id": conflicted.id},
        )
    )

    assert payload["ok"] is True
    session.refresh(conflicted)
    instance, stages = OrchestrationService(session)._resolve_stages(conflicted)
    assert conflicted.state is TicketState.DONE
    assert parse_stage_map(instance, stages)["done"] is StageStatus.DONE
    assert conflicted.blocking_issues == ""
    assert conflicted.block_kind is None


def test_landing_by_hand_that_still_conflicts_leaves_the_block(session, conflicted):
    """The tool only finishes what actually landed; a conflict stays a block."""
    from loregarden.mcp.ticket_ops_tools import execute_ticket_ops_tool
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

    conflicted.git_automation_json = json.dumps({"auto_resolve_conflicts": False})
    session.add(conflicted)
    session.commit()
    _land(session, conflicted)

    payload = json.loads(
        execute_ticket_ops_tool(
            "loregarden_land_ticket",
            session,
            OrchestrationCallbackService(session),
            {"ticket_id": conflicted.id},
        )
    )

    assert payload["ok"] is False
    session.refresh(conflicted)
    assert conflicted.state is TicketState.BLOCKED


def _blocked_on_landing(session, conflicted):
    conflicted.git_automation_json = json.dumps({"auto_resolve_conflicts": False})
    session.add(conflicted)
    session.commit()
    _land(session, conflicted)
    session.refresh(conflicted)
    assert conflicted.state is TicketState.BLOCKED, "the fixture must block on the landing"


def test_the_sweep_finishes_a_ticket_whose_conflict_was_resolved(
    session, conflicted, workspace, repo
):
    """Resolved on the branch, never landed: the sweep lands it and finishes (928)."""
    from loregarden.services.terminal_stage_sweep import finish_tickets_whose_landing_cleared

    _blocked_on_landing(session, conflicted)
    target = resolve_target_branch(session, conflicted, workspace, repo_root=repo)
    _resolve_by_hand(repo, conflicted, target)

    finished = finish_tickets_whose_landing_cleared(session, ticket_id=conflicted.id)

    session.refresh(conflicted)
    assert [t.id for t in finished] == [conflicted.id]
    assert conflicted.state is TicketState.DONE
    assert git(repo, "merge-base", "--is-ancestor", conflicted.branch, target).returncode == 0
    assert "finished_cleared_landing" in decision_kinds(session, conflicted.id)


def test_the_sweep_leaves_a_conflict_that_is_still_there(session, conflicted):
    """No retry of an unchanged conflict: git says no, so the ticket is not touched."""
    from loregarden.services.terminal_stage_sweep import finish_tickets_whose_landing_cleared

    _blocked_on_landing(session, conflicted)
    before = len(decision_kinds(session, conflicted.id))

    assert finish_tickets_whose_landing_cleared(session, ticket_id=conflicted.id) == []

    session.refresh(conflicted)
    assert conflicted.state is TicketState.BLOCKED
    assert len(decision_kinds(session, conflicted.id)) == before


def test_relanding_keeps_the_commit_that_landed_it(session, conflicted, workspace, repo):
    """`landed_sha` is the commit that landed the branch; landing again must not
    swap it for the branch tip, as 782's did (26be6aea → 41f216f9)."""
    from loregarden.services.land_ticket import land_ticket

    _blocked_on_landing(session, conflicted)
    target = resolve_target_branch(session, conflicted, workspace, repo_root=repo)
    _resolve_by_hand(repo, conflicted, target)
    first = land_ticket(session, conflicted, workspace)
    assert first.ok and first.landed_sha, "the first landing must merge"

    land_ticket(session, conflicted, workspace)

    session.refresh(conflicted)
    assert conflicted.landed_sha == first.landed_sha
