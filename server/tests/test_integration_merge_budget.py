"""A merge turn spends an attempt only when it tried (lg-milestone-that-959).

2026-10-13 12:56 UTC: the sweep handed both blobert trees to merge turns, the
agent CLI answered "You've hit your usage limit", and each tip's two attempts
were spent within a minute without an agent ever seeing the conflict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    BlockKind,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services import integration_merge_agent
from loregarden.services.integration_merge_budget import (
    HARNESS_RETRY_BASE,
    HARNESS_RETRY_CAP,
    INTEGRATION_MERGE_STAGE,
    harness_retry_at,
    merge_turn_refusal,
    refused_turns,
)
from loregarden.services.target_branch import IntegrationConflictError, resolve_target_branch
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, make_repo

BRANCH = "integration/ms-budget"

#: What the repair agent's CLI said on 2026-10-13, word for word.
CURSOR_QUOTA = (
    "ActionRequiredError: You've hit your usage limit You've saved $496 on API model usage "
    "this month with Pro. Switch to a different model or set a Spend Limit to continue with "
    "Auto. Your usage limits will reset when your monthly cycle ends on 10/20/2026."
)


class Tree:
    def __init__(self, session: Session, workspace: Workspace, repo: Path, root: Ticket, child):
        self.session, self.workspace, self.repo, self.root, self.child = (
            session,
            workspace,
            repo,
            root,
            child,
        )

    @property
    def tip(self) -> str:
        return git(self.repo, "rev-parse", BRANCH).stdout.strip()

    def resolve(self) -> str:
        return resolve_target_branch(self.session, self.child, self.workspace, repo_root=self.repo)

    def runs(self) -> list[AgentRun]:
        self.session.expire_all()
        return list(
            self.session.exec(
                select(AgentRun)
                .where(
                    AgentRun.ticket_id == self.root.id,
                    AgentRun.stage_key == INTEGRATION_MERGE_STAGE,
                )
                .order_by(AgentRun.started_at)
            ).all()
        )

    def card(self) -> Approval:
        [card] = self.session.exec(
            select(Approval).where(
                Approval.ticket_id == self.root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
            )
        ).all()
        return card

    def refusal(self) -> str:
        return merge_turn_refusal(self.session, self.workspace, self.root, branch_sha=self.tip)


@pytest.fixture
def tree(isolated_db, tmp_path: Path):
    """Milestone `ms-budget` whose integration branch and `main` both rewrote one line."""
    repo = make_repo(tmp_path)
    with Session(isolated_db) as session:
        workspace = Workspace(slug="budget", name="budget", repo_path=str(repo))
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        tickets: dict[str, Ticket] = {}
        for external_id, kind, parent in (
            ("ms-budget", WorkItemType.MILESTONE, None),
            ("tk-budget", WorkItemType.TASK, "ms-budget"),
        ):
            ticket = Ticket(
                external_id=external_id,
                workspace_id=workspace.id,
                title=external_id,
                state=TicketState.IN_PROGRESS,
                work_item_type=kind,
                parent_ticket_id=tickets[parent].id if parent else None,
            )
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            tickets[external_id] = ticket
        built = Tree(session, workspace, repo, tickets["ms-budget"], tickets["tk-budget"])
        assert built.resolve() == BRANCH
        commit_on(repo, BRANCH, "shared.txt", "branch side\n")
        commit_on(repo, "main", "shared.txt", "main side\n")
        yield built


def _quota(*_args, **_kwargs) -> str:
    raise RuntimeError(CURSOR_QUOTA)


def _giving_up(*_args, **_kwargs) -> str:
    return "This needs a person."


def _resolving(*_args, tree: Path, **_kwargs) -> str:
    (tree / "shared.txt").write_text("branch side\nmain side\n")
    git(tree, "add", "shared.txt")
    return "Kept both sides."


def _age(tree: Tree, run: AgentRun, by: timedelta) -> None:
    """Move a run's finish back in time, as if the backoff had elapsed."""
    run.finished_at = (run.finished_at or datetime.now(timezone.utc)) - by
    tree.session.add(run)
    tree.session.commit()


# --- the budget -----------------------------------------------------------------


def test_a_usage_limited_turn_spends_no_attempt(tree: Tree):
    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_quota):
        with pytest.raises(IntegrationConflictError, match="harness failure, not counted"):
            tree.resolve()

    [run] = tree.runs()
    assert (run.status, run.failure_kind) == (RunStatus.FAILED, BlockKind.HARNESS)
    assert "usage limit" in run.stderr
    assert refused_turns(tree.session, tree.root, branch_sha=tree.tip) == []


def test_the_card_says_when_the_next_turn_runs_and_no_turn_runs_before_then(tree: Tree):
    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_quota) as turn:
        for _ in range(3):
            with pytest.raises(IntegrationConflictError):
                tree.resolve()

    assert turn.call_count == 1, "the backoff holds the next turn"
    impact = tree.card().impact
    assert "could not attempt the merge" in impact
    assert "next turn runs after" in impact
    assert "0 of 2 attempts used" in tree.refusal()


def test_after_the_backoff_a_real_turn_still_has_its_budget(tree: Tree):
    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_quota):
        with pytest.raises(IntegrationConflictError):
            tree.resolve()
    [failed] = tree.runs()
    _age(tree, failed, HARNESS_RETRY_BASE + timedelta(minutes=1))

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_resolving):
        assert tree.resolve() == BRANCH

    assert [run.status for run in tree.runs()] == [RunStatus.FAILED, RunStatus.SUCCEEDED]
    git(tree.repo, "merge-base", "--is-ancestor", "main", BRANCH)


def test_a_refused_resolution_spends_an_attempt(tree: Tree):
    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_giving_up) as turn:
        for _ in range(3):
            with pytest.raises(IntegrationConflictError):
                tree.resolve()

    assert turn.call_count == 2
    assert [run.failure_kind for run in tree.runs()] == [BlockKind.WORK, BlockKind.WORK]
    assert "2 automatic merge attempt(s)" in tree.card().impact


@pytest.mark.parametrize(
    ("raised", "kind"),
    [
        (FileNotFoundError(2, "No such file or directory", "cursor-agent"), BlockKind.HARNESS),
        (ValueError("Unknown integration merge agent: repair"), BlockKind.HARNESS),
        (TimeoutError("Integration merge timed out after 900s"), BlockKind.WORK),
        (RuntimeError("Integration merge returned an empty response"), BlockKind.WORK),
    ],
    ids=["cli-missing", "misconfigured", "timed-out", "ran-and-said-nothing"],
)
def test_how_the_agent_failed_decides_whether_it_counts(tree: Tree, raised, kind):
    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=raised):
        with pytest.raises(IntegrationConflictError):
            tree.resolve()

    [run] = tree.runs()
    assert run.failure_kind is kind


def test_a_crash_settles_the_run_as_harness_and_still_surfaces(tree: Tree):
    with mock.patch.object(
        integration_merge_agent, "_merge_in_worktree", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(RuntimeError, match="boom"):
            tree.resolve()

    [run] = tree.runs()
    assert (run.status, run.failure_kind) == (RunStatus.FAILED, BlockKind.HARNESS)
    assert "crashed: boom" in run.stderr


# --- the backoff ----------------------------------------------------------------


def _failed_run(tree: Tree, kind: BlockKind | None, finished: datetime, stderr: str = "") -> None:
    tree.session.add(
        AgentRun(
            run_code=f"run_{kind}_{finished.timestamp():.0f}",
            ticket_id=tree.root.id,
            workspace_id=tree.workspace.id,
            agent_id="repair",
            stage_key=INTEGRATION_MERGE_STAGE,
            status=RunStatus.FAILED,
            failure_kind=kind,
            start_head_sha=tree.tip,
            started_at=finished - timedelta(seconds=5),
            finished_at=finished,
            stderr=stderr,
        )
    )
    tree.session.commit()


T0 = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _retry_at(tree: Tree) -> datetime | None:
    return harness_retry_at(tree.session, tree.root, branch_sha=tree.tip)


def test_the_backoff_doubles_with_each_harness_failure_and_caps(tree: Tree):
    assert _retry_at(tree) is None
    _failed_run(tree, BlockKind.HARNESS, T0)
    assert _retry_at(tree) == T0 + HARNESS_RETRY_BASE
    _failed_run(tree, BlockKind.HARNESS, T0 + timedelta(hours=1))
    assert _retry_at(tree) == T0 + timedelta(hours=1) + 2 * HARNESS_RETRY_BASE
    for hour in range(2, 12):
        _failed_run(tree, BlockKind.HARNESS, T0 + timedelta(hours=hour))
    assert _retry_at(tree) == T0 + timedelta(hours=11) + HARNESS_RETRY_CAP


def test_a_turn_that_tried_resets_the_backoff(tree: Tree):
    _failed_run(tree, BlockKind.HARNESS, T0)
    _failed_run(tree, BlockKind.HARNESS, T0 + timedelta(hours=1))
    _failed_run(tree, BlockKind.WORK, T0 + timedelta(hours=2))
    assert _retry_at(tree) is None
    _failed_run(tree, BlockKind.HARNESS, T0 + timedelta(hours=3))
    assert _retry_at(tree) == T0 + timedelta(hours=3) + HARNESS_RETRY_BASE


def test_a_usage_limit_that_names_its_reset_is_waited_out(tree: Tree):
    resets = T0 + timedelta(days=2)
    _failed_run(
        tree,
        BlockKind.HARNESS,
        T0,
        stderr=f"the merge turn failed (Claude AI usage limit reached|{resets.timestamp():.0f})",
    )
    assert _retry_at(tree) == resets


def test_runs_from_before_the_kind_existed_count_as_neither(tree: Tree):
    _failed_run(tree, None, T0)
    _failed_run(tree, None, T0 + timedelta(minutes=1))
    assert refused_turns(tree.session, tree.root, branch_sha=tree.tip) == []
    assert _retry_at(tree) is None
    assert tree.refusal() == ""
