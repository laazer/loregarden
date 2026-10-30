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
    attempt_file_diff,
    decline_fanout,
    launch_fanout,
    promote_attempt,
)
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select
from tests.worktree_helpers import git, land_branch_onto, make_repo

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
        # COMMITTED, as a real attempt's run is (lg-external-harness-1026
        # implement, AC18). Leaving the file uncommitted made every attempt's
        # tree hold work that exists in exactly one place, and the receipt gate
        # AC14 routes this site through refuses to delete one of those — which
        # is the gate working, not a regression. An attempt whose work is only
        # in its working tree is covered on purpose by
        # `test_worktree_gate_chokepoint.py`, where the refusal is the subject.
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", f"{worktree.branch} attempt")
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


def test_the_manifests_are_comparable_because_they_share_a_base(session, ticket):
    group, _ = _launch(session, ticket, count=2)

    diffs = attempt_diffs(session, group["id"])

    assert len(diffs) == 2
    for diff in diffs:
        assert [f["path"] for f in diff["files"]] == ["answer.txt"]
        assert diff["files_changed"] == 1
        assert diff["additions"] == 1
        assert diff["deletions"] == 0


def test_a_file_s_patch_is_fetched_on_demand(session, ticket):
    group, _ = _launch(session, ticket, count=2)
    first, second = group["attempts"]

    one = attempt_file_diff(session, group["id"], first["id"], "answer.txt")
    two = attempt_file_diff(session, group["id"], second["id"], "answer.txt")

    assert first["branch"] in one["patch"]
    # Different attempts, different content — the thing being compared.
    assert one["patch"] != two["patch"]


def test_a_file_diff_for_a_foreign_attempt_is_refused(session, ticket):
    group, _ = _launch(session, ticket, count=2)

    with pytest.raises(FanoutError, match="does not belong"):
        attempt_file_diff(session, group["id"], "not-an-attempt", "answer.txt")


def test_discarding_twice_is_harmless(session, ticket, repo):
    """Cleanup has to be idempotent: settle races and startup reconciliation
    can both reach the same worktree."""
    group, _ = _launch(session, ticket, count=2)
    decline_fanout(session, group["id"])

    # The second sweep finds directories already gone and branches already
    # deleted, and must not raise.
    from loregarden.models.domain import StageFanoutGroup
    from loregarden.services.stage_fanout_service import _discard_attempts

    row = session.get(StageFanoutGroup, group["id"])
    again = _discard_attempts(session, row, keep_attempt_id=None)

    assert len(again) == 2


@pytest.mark.parametrize(
    "refusal_name", ["OUTSIDE_BASE", "REMOVE_RAISED"], ids=["outside_base", "remove_raised"]
)
def test_a_losing_branch_survives_a_failed_worktree_removal(
    session, ticket, repo, caplog, refusal_name
):
    """A losing attempt whose worktree removal fails must keep its branch: the
    checkout's commits are reachable from nothing else, so force-deleting the
    branch (`branch -D`) would strand them.

    The oracle is the *absence of the `branch -D` call*, not the surviving
    branch. A refused `remove_checkout` leaves the checkout registered with git,
    and git refuses to delete a branch checked out in a registered worktree — so
    "the branch is still there" is satisfied by git's own refusal and passes
    with the guard removed. Only the uninvoked `branch -D` distinguishes the
    guard running from git saving us.

    A sibling attempt whose removal succeeds must still be discarded normally —
    the loop must not stop at the first failure.

    AC21 — run for BOTH `CleanupRefusal` members. `_discard_attempts` reads
    `refusal is not None`, not which member it is: an out-of-base path leaves
    the checkout just as owned as a permission error does, so the guard
    lg-external-harness-1166 added must hold for either cause. Parametrized
    rather than asserted once, because a branch per member here would be the
    place the never-retryable cause quietly regained the force-delete.
    """
    import logging

    from loregarden.services import stage_fanout_service
    from loregarden.services.directory_release import DirectoryReleaseFailed, ReleaseDisposition
    from loregarden.services.git_subprocess import run_git as real_run_git
    from loregarden.services.worktree_service import CleanupRefusal, WorktreeService

    group, _ = _launch(session, ticket, count=2)
    failing_attempt_id = group["attempts"][0]["id"]
    ok_attempt_id = group["attempts"][1]["id"]
    failing_branch = group["attempts"][0]["branch"]
    ok_branch = group["attempts"][1]["branch"]

    # Read the failing attempt's worktree row BEFORE the call: the sibling's row
    # is deleted by a successful cleanup, and these identifiers are what the
    # warning must name.
    failing_worktree_id = group["attempts"][0]["worktree_id"]
    failing_worktree_path = session.get(Worktree, failing_worktree_id).worktree_path

    # The sibling's work is taken into `main` (lg-external-harness-1026
    # AC15/AC18): the gate removes a checkout whose commits something else
    # already reaches, and refuses one whose commits are reachable from nowhere
    # else. Without this the sibling is refused too and the uninvoked
    # `branch -D` below would prove nothing about the guard.
    land_branch_onto(repo, ok_branch, "main")

    refusal = getattr(CleanupRefusal, refusal_name)
    real_cleanup = WorktreeService.remove_checkout

    def flaky_cleanup(self, worktree):
        # Raised, never returned and never `False`: lg-external-harness-1026
        # made the seam a command, so any value it answers reads as "the
        # checkout is gone" and a stub returning one would read as settled.
        if worktree.branch == failing_branch:
            raise DirectoryReleaseFailed(
                "the removal failed",
                disposition=(
                    ReleaseDisposition.STILL_PARKED
                    if refusal is CleanupRefusal.REMOVE_RAISED
                    else ReleaseDisposition.INTACT
                ),
                refusal=refusal,
                uncleaned_path=worktree.worktree_path,
                parked_path=(
                    f"{worktree.worktree_path}.parked"
                    if refusal is CleanupRefusal.REMOVE_RAISED
                    else ""
                ),
            )
        return real_cleanup(self, worktree)

    git_calls: list[list[str]] = []

    def spying_run_git(args, **kwargs):
        git_calls.append(list(args))
        return real_run_git(args, **kwargs)

    with caplog.at_level(logging.WARNING):
        with (
            patch.object(WorktreeService, "remove_checkout", flaky_cleanup),
            patch.object(stage_fanout_service, "run_git", spying_run_git),
        ):
            settled = decline_fanout(session, group["id"])

    # (a) Causality: the guard stopped the force-delete from ever being issued
    # for the attempt whose removal failed, while the sibling's really ran.
    assert ["branch", "-D", failing_branch] not in git_calls, (
        "branch -D was issued for an attempt whose worktree removal failed"
    )
    assert ["branch", "-D", ok_branch] in git_calls, (
        "the sibling attempt's branch must still be force-deleted"
    )

    # (b) The failure is observable, and names the attempt, the worktree row and
    # its path — not just the branch, which the delete-failure warning also
    # names.
    identifying = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and str(failing_attempt_id) in r.getMessage()
        and str(failing_worktree_id) in r.getMessage()
        and failing_worktree_path in r.getMessage()
    ]
    assert len(identifying) == 1, (
        "expected exactly one warning naming the attempt, worktree row and path; got "
        f"{[r.getMessage() for r in identifying]}"
    )

    # (c) No delete-failure warning for the failing branch: such a record only
    # exists when the delete was attempted.
    assert not [
        r
        for r in caplog.records
        if "Could not delete discarded fan-out branch" in r.getMessage()
        and failing_branch in r.getMessage()
    ], "the force-delete was attempted and refused, rather than skipped"

    # (d) End state, and the loop did not stop at the first failure.
    branches = git(repo, "branch", "--list").stdout
    assert failing_branch in branches, "branch force-deleted despite a failed removal"
    assert ok_branch not in branches, "the sibling discard must still happen"
    assert set(settled["discarded_attempts"]) == {failing_attempt_id, ok_attempt_id}


def _keeper_takes(session, keeper_attempt, *loser_branches: str) -> None:
    """Make `keeper_branch` reach every loser's tip, keeping its own content.

    The condition lg-external-harness-1026 AC15 names for a losing attempt's
    checkout to be removable: the promoted keeper already contains its tip, so
    nothing the loser committed is lost with the directory. `-s ours` is the
    whole point — the keeper's answer wins, and the loser's commits stop being
    reachable from nowhere else. A loser the keeper does NOT reach is refused
    and keeps its branch, which is covered in
    `test_worktree_gate_chokepoint.py`.
    """
    # In the keeper's OWN checkout: its branch is checked out there, and git
    # refuses a second worktree on a branch one already holds.
    tree = Path(session.get(Worktree, keeper_attempt["worktree_id"]).worktree_path)
    for loser in loser_branches:
        git(tree, "merge", "-q", "--no-ff", "-s", "ours", "-m", f"keeper takes {loser}", loser)


def test_promoting_lands_the_winner_and_removes_every_loser(session, ticket, workspace, repo):
    group, _ = _launch(session, ticket, count=3)
    winner = group["attempts"][1]
    loser_branches = [a["branch"] for a in group["attempts"] if a["id"] != winner["id"]]
    _keeper_takes(session, winner, *loser_branches)

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


def test_declining_restores_the_stage_and_keeps_what_nobody_chose(session, ticket, workspace, repo):
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
    # BEHAVIOUR THE SPEC NO LONGER WANTS (lg-external-harness-1026 AC14/AC15).
    # A declined group promoted nobody, so nothing has chosen any attempt's
    # work: the receipt gate refuses every removal and the branches stay, which
    # is the whole point of routing this site — `_discard_attempts` used to
    # remove the checkout and then force-delete the only ref keeping those
    # commits reachable. What this test is still named for holds: the stage is
    # restored and the ticket's own tree took nothing.
    branches = git(repo, "branch", "--list").stdout
    assert "attempt-" in branches, "the gate's refusal must keep the losers' only refs"
    assert _live_worktrees_for(session, group["id"], keep=None)
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


def test_a_group_with_a_failed_attempt_is_still_settleable(session, ticket, workspace, repo):
    """One agent giving up must not strand the other two: the comparison is
    still worth having, and the group still has to be closable."""
    group, _ = _launch(session, ticket, count=3, fail_indexes=(0,))
    survivor = next(a for a in group["attempts"] if a["status"] == "succeeded")
    _keeper_takes(
        session,
        survivor,
        *[a["branch"] for a in group["attempts"] if a["id"] != survivor["id"]],
    )

    settled = promote_attempt(session, group["id"], survivor["id"])

    assert settled["outcome"] == StageFanoutOutcome.PROMOTED.value
    assert not _live_worktrees_for(session, group["id"], keep=survivor["id"])
    branches = git(repo, "branch", "--list").stdout
    failed = next(a for a in group["attempts"] if a["status"] == "failed")
    assert failed["branch"] not in branches


def test_a_fanout_does_not_consume_execution_slots(session, ticket):
    """Attempts run outside the slot pool by design — see the checkpoint on 347.
    The invariant that matters is that they neither claim nor leak one."""
    from loregarden.models.domain import AgentSlot
    from loregarden.services.parallel_queue import ParallelQueueService

    ParallelQueueService(session, max_concurrent=3).initialize_slots()
    group, _ = _launch(session, ticket, count=2)
    decline_fanout(session, group["id"])

    slots = session.exec(select(AgentSlot)).all()
    assert slots, "the pool should still exist"
    assert all(slot.is_available for slot in slots)
