"""Fanning one stage into N attempts and keeping exactly one.

The properties worth pinning are the ones that cost real money or real work if
they are wrong: the attempts must be genuinely isolated (the orchestrator
commits whole trees, so two attempts sharing one would sweep each other), the
comparison must be against a common base, and settling — either way — must not
leave a worktree or a branch behind.
"""

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    RunStatus,
    StageFanoutAttemptStatus,
    StageFanoutGroupStatus,
    StageFanoutOutcome,
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
from loregarden.services.stage_fanout_service import (
    FanoutError,
    attempt_diffs,
    decline_fanout,
    launch_fanout,
    promote_attempt,
)
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo

STAGES = [
    WorkflowStageDef(
        key="implement",
        name="Implement",
        stage_type="agent",
        order=1,
        agent_id="backend_implementer",
    ),
    WorkflowStageDef(key="done", name="Done", order=2, terminal=True, stage_type="agent"),
]

PASS_REPORT = (
    '<<<LOREGARDEN_STAGE_REPORT>>>\n{"status": "pass", "confidence": 0.9}\n<<<END_STAGE_REPORT>>>\n'
)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    template = WorkflowTemplate(
        slug=f"fanout-test-{uuid4()}",
        name="Fan-out test template",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps([{"from": "implement", "to": "done", "when": "pass"}]),
    )
    session.add(template)
    session.commit()
    session.refresh(template)

    ticket = Ticket(
        external_id="LG-1",
        workspace_id=workspace.id,
        title="Add the thing",
        branch="loregarden/lg-1-add-the-thing",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.PENDING,
        next_agent="backend_implementer",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(STAGES),
        )
    )
    session.commit()
    return ticket


def _fake_execute(*, fail_indexes=()):
    """Stand in for the agent: write one file in whatever tree the run got."""
    seen: list[str] = []

    def execute(self, run, ticket, *, advance_workflow=True, skip_git_branch=False):
        worktree = self.session.get(Worktree, run.worktree_id)
        root = worktree.worktree_path
        # Keyed on the branch, not on arrival order: the attempts run
        # concurrently, so arrival order is not the attempt number.
        index = int(worktree.branch.rsplit("-", 1)[-1]) - 1
        seen.append(root)
        (Path(root) / "answer.txt").write_text(f"{worktree.branch} says hello\n")
        failing = index in fail_indexes
        return self.orchestration.complete_run(
            run,
            status=RunStatus.FAILED if failing else RunStatus.SUCCEEDED,
            stdout="" if failing else PASS_REPORT,
            stderr="agent gave up" if failing else "",
            advance_workflow=False,
        )

    execute.seen = seen
    return execute


def _launch(session, ticket, count=3, **kwargs):
    fake = _fake_execute(**kwargs)
    with patch.object(CliAgentExecutor, "execute", fake):
        group = launch_fanout(session, ticket, "implement", count)
    return group, fake


def test_each_attempt_runs_in_its_own_worktree_and_branch(session, ticket):
    group, fake = _launch(session, ticket)

    assert len(group["attempts"]) == 3
    assert len(set(fake.seen)) == 3, "attempts shared a tree"
    branches = {a["branch"] for a in group["attempts"]}
    assert branches == {
        "loregarden/lg-1-add-the-thing-attempt-1",
        "loregarden/lg-1-add-the-thing-attempt-2",
        "loregarden/lg-1-add-the-thing-attempt-3",
    }
    assert all(a["status"] == StageFanoutAttemptStatus.SUCCEEDED.value for a in group["attempts"])


def test_a_failing_attempt_is_recorded_without_sinking_the_others(session, ticket):
    group, _ = _launch(session, ticket, fail_indexes=(1,))

    statuses = [a["status"] for a in group["attempts"]]
    assert statuses.count(StageFanoutAttemptStatus.SUCCEEDED.value) == 2
    assert statuses.count(StageFanoutAttemptStatus.FAILED.value) == 1
    failed = next(a for a in group["attempts"] if a["status"] == "failed")
    assert failed["failure_details"]


def test_the_diffs_are_comparable_because_they_share_a_base(session, ticket):
    group, _ = _launch(session, ticket, count=2)

    diffs = attempt_diffs(session, group["id"])

    assert len(diffs) == 2
    for diff in diffs:
        assert "answer.txt" in diff["patch"]
        assert diff["files_changed"] == 1
    # Different attempts, different content — which is the thing being compared.
    assert diffs[0]["patch"] != diffs[1]["patch"]


def test_promoting_lands_the_winner_and_removes_every_loser(session, ticket, workspace, repo):
    group, _ = _launch(session, ticket, count=3)
    winner = group["attempts"][1]
    loser_branches = [a["branch"] for a in group["attempts"] if a["id"] != winner["id"]]

    settled = promote_attempt(session, group["id"], winner["id"])

    assert settled["outcome"] == StageFanoutOutcome.PROMOTED.value
    assert settled["status"] == StageFanoutGroupStatus.SETTLED.value
    assert settled["winner_attempt_id"] == winner["id"]
    # The winner's work is on the ticket's branch.
    ticket_root = resolve_ticket_root(session, ticket, workspace)
    assert (ticket_root / "answer.txt").read_text() == f"{winner['branch']} says hello\n"
    # And no losing branch or worktree survives.
    branches = git(repo, "branch", "--list").stdout
    for branch in loser_branches:
        assert branch not in branches
    assert not _live_worktrees_for(session, group["id"], keep=winner["id"])
    session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.DONE


def test_declining_restores_the_stage_and_leaves_nothing_behind(session, ticket, workspace, repo):
    before_stage = ticket.workflow_stage_key
    before_agent = ticket.next_agent
    group, _ = _launch(session, ticket, count=2)

    settled = decline_fanout(session, group["id"], reason="none of these")

    assert settled["outcome"] == StageFanoutOutcome.DECLINED.value
    assert settled["declined_reason"] == "none of these"
    session.refresh(ticket)
    assert ticket.workflow_stage_key == before_stage
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert ticket.next_agent == before_agent
    branches = git(repo, "branch", "--list").stdout
    assert "attempt-" not in branches
    assert not _live_worktrees_for(session, group["id"], keep=None)
    # The ticket's own tree never took any of it.
    assert not (resolve_ticket_root(session, ticket, workspace) / "answer.txt").exists()


def test_a_second_fanout_is_refused_while_one_is_unsettled(session, ticket):
    _launch(session, ticket, count=2)

    with pytest.raises(FanoutError, match="already has an unsettled fan-out"):
        _launch(session, ticket, count=2)


def test_settling_twice_is_refused(session, ticket):
    group, _ = _launch(session, ticket, count=2)
    decline_fanout(session, group["id"])

    with pytest.raises(FanoutError, match="already settled"):
        promote_attempt(session, group["id"], group["attempts"][0]["id"])


@pytest.mark.parametrize("count", [1, 6])
def test_the_attempt_count_is_bounded_at_both_ends(session, ticket, count):
    with pytest.raises(FanoutError, match="attempt_count"):
        launch_fanout(session, ticket, "implement", count)


def _live_worktrees_for(session, group_id, keep):
    """Worktree directories still on disk for this group's losing attempts."""
    from pathlib import Path

    from loregarden.models.domain import StageFanoutAttempt

    attempts = session.exec(
        select(StageFanoutAttempt).where(StageFanoutAttempt.group_id == group_id)
    ).all()
    live = []
    for attempt in attempts:
        if attempt.id == keep or not attempt.worktree_id:
            continue
        worktree = session.get(Worktree, attempt.worktree_id)
        if worktree and Path(worktree.worktree_path).exists():
            live.append(worktree.worktree_path)
    return live
