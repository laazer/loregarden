"""Where a stage runs: the ticket's worktree, not the shared checkout.

The shared checkout switching branches under every run is the mechanism behind
both known failure modes — a crash leaves it half-applied, and two tickets
cannot run at once. These tests pin that ticket execution never moves it.
"""

from pathlib import Path

import pytest
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session
from tests.worktree_helpers import head_branch, make_repo, make_run, make_ticket


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
    return make_ticket(session, workspace)


def test_a_stage_runs_in_the_ticket_worktree_and_leaves_the_checkout_alone(
    session, workspace, ticket, repo
):
    run = make_run(session, workspace, ticket, "r1")

    root = resolve_execution_root(session, run, ticket, workspace)

    assert root != repo
    assert head_branch(root) == "loregarden/lg-1-add-the-thing"
    # The branch was created by `git worktree add -b`, so the shared checkout
    # never left main.
    assert head_branch(repo) == "main"
    assert run.worktree_id


def test_the_next_stage_of_the_same_ticket_lands_in_the_same_tree(session, workspace, ticket, repo):
    first = resolve_execution_root(
        session, make_run(session, workspace, ticket, "r1"), ticket, workspace
    )
    (first / "stage-one.txt").write_text("work\n")

    second = resolve_execution_root(
        session, make_run(session, workspace, ticket, "r2"), ticket, workspace
    )

    assert second == first
    assert (second / "stage-one.txt").exists()


def test_a_run_that_already_has_a_worktree_keeps_it(session, workspace, ticket, repo):
    """The parallel queue and stage fan-out assign a worktree before dispatch;
    resolution must not hand those runs the ticket's shared tree instead."""
    run = make_run(session, workspace, ticket, "r1")
    service = WorktreeService(session, repo_path=str(repo))
    own = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        parent_branch="main",
        branch="attempt-1",
    )
    assert own is not None
    run.worktree_id = own.id
    session.add(run)
    session.commit()

    root = resolve_execution_root(session, run, ticket, workspace)

    assert root == Path(own.worktree_path)


def test_turning_the_worktree_policy_off_keeps_the_shared_checkout(
    session, workspace, ticket, repo
):
    ticket.git_automation_json = serialize_override({"worktree": False})
    session.add(ticket)
    session.commit()
    run = make_run(session, workspace, ticket, "r1")

    root = resolve_execution_root(session, run, ticket, workspace)

    assert root == repo
    assert not run.worktree_id


def test_two_tickets_resolve_to_two_trees(session, workspace, ticket, repo):
    other = Ticket(
        external_id="LG-2",
        workspace_id=workspace.id,
        title="Other thing",
        branch="loregarden/lg-2-other-thing",
    )
    session.add(other)
    session.commit()
    session.refresh(other)

    one = resolve_execution_root(
        session, make_run(session, workspace, ticket, "r1"), ticket, workspace
    )
    two = resolve_execution_root(
        session, make_run(session, workspace, other, "r2"), other, workspace
    )

    assert one != two
    assert head_branch(repo) == "main"


# ---------------------------------------------------------------------------
# lg-milestone-that-918: being told the target instead of resolving it.
#
# The single-agent dispatch resolves the same target branch a second line
# later, and resolving is a write against refs every worktree shares. So a
# caller that has already answered the question can hand the answer over. It is
# one optional keyword and nothing else: `stale_target_ok` stays, the chat path
# stays byte-identical, and `resolve_target=None` behaves exactly as today.
# ---------------------------------------------------------------------------


def test_being_told_the_target_resolves_nothing_itself(session, workspace, ticket, repo):
    """AC9. The point of the parameter is the resolution that does *not*
    happen: the module's own binding is replaced by one that fails the test if
    it is reached, and the tree is still cut from the branch it was told.
    """
    from unittest import mock

    from loregarden.services import ticket_worktree

    told: list[str] = []

    def once() -> str:
        told.append("asked")
        return "main"

    def never(*args, **kwargs):
        raise AssertionError("a told target must not be resolved again")

    run = make_run(session, workspace, ticket, "r1")
    with mock.patch.object(ticket_worktree, "resolve_target_branch", never):
        root = resolve_execution_root(session, run, ticket, workspace, resolve_target=once)

    assert told == ["asked"], f"the caller's resolver was asked {len(told)} times"
    assert root != repo
    assert head_branch(root) == ticket.branch
    assert head_branch(repo) == "main", "and the shared checkout was not moved"


def test_a_told_target_and_a_tolerated_stale_one_are_refused_together(
    session, workspace, ticket, repo
):
    """AC9. They encode two different policies for the same question —
    `merge_conflicts`, and whether a merge turn gets a seat — so honouring one
    silently would hand a caller an answer it did not ask for. No production
    caller passes both; the refusal says which two it is refusing.
    """
    run = make_run(session, workspace, ticket, "r1")

    with pytest.raises(ValueError) as raised:
        resolve_execution_root(
            session,
            run,
            ticket,
            workspace,
            stale_target_ok=True,
            resolve_target=lambda: "main",
        )

    message = str(raised.value)
    assert "stale_target_ok" in message and "resolve_target" in message, message


def test_the_default_still_resolves_through_its_own_binding(session, workspace, ticket, repo):
    """AC9. With the keyword omitted nothing changes: the module resolves for
    itself, against the shared checkout, asking for the conflicts a stage run
    must not start without.
    """
    from unittest import mock

    from loregarden.services import ticket_worktree
    from loregarden.services.target_branch import resolve_target_branch as real

    seen: list[dict] = []

    def spy(session_, ticket_, workspace_, *, repo_root, **extra):
        seen.append({"repo_root": Path(repo_root), **extra})
        return real(session_, ticket_, workspace_, repo_root=repo_root, **extra)

    run = make_run(session, workspace, ticket, "r1")
    with mock.patch.object(ticket_worktree, "resolve_target_branch", spy):
        root = resolve_execution_root(session, run, ticket, workspace)

    assert root != repo
    assert seen == [{"repo_root": repo, "merge_conflicts": True}], seen


# ---------------------------------------------------------------------------
# lg-milestone-that-918, test-break: a told target that is never needed.
#
# REQ-3 pins *where* `resolve_target()` is called — after the `run.worktree_id`
# early return and after the `not config.worktree` early return — because the
# caller's resolver is the memo, and evaluating it writes shared refs. A mutant
# that calls it at the top of the function instead is caught at the dispatch
# level by AC2(a) and AC2(b), but nothing asks `resolve_execution_root` itself,
# and AC2(b) reaches the `worktree_id` return only with `skip_git_branch=True`
# — a path where `cli` never builds a memo at all, so the early return is not
# actually what is being tested there.
# ---------------------------------------------------------------------------


def test_a_run_that_already_has_a_tree_is_never_asked_for_a_target(session, workspace, ticket):
    """AC2(b)/AC9. The run carries its worktree, so the answer is not needed
    and must not be computed: resolving writes refs that every worktree of the
    repository shares, and every member of a fan-out would write them.
    """
    run = make_run(session, workspace, ticket, "p1")
    service = WorktreeService(session, repo_path=str(workspace.repo_path))
    own = service.create_worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        parent_branch="main",
        branch="attempt-1",
    )
    assert own is not None
    run.worktree_id = own.id
    session.add(run)
    session.commit()

    def never() -> str:
        raise AssertionError("a run holding its own tree must not be asked for a target")

    root = resolve_execution_root(session, run, ticket, workspace, resolve_target=never)

    assert root == Path(own.worktree_path)


def test_a_ticket_with_worktrees_off_is_never_asked_for_a_target(session, workspace, ticket, repo):
    """AC2(a)/AC9. Worktrees off means the run executes in the shared checkout
    and there is nothing to cut from a target, so the question is not asked.
    This is the path the dirty-park case runs through: a run parked before it
    starts must not have written to shared refs on its way there.
    """
    ticket.git_automation_json = serialize_override({"worktree": False})
    session.add(ticket)
    session.commit()
    run = make_run(session, workspace, ticket, "r1")

    def never() -> str:
        raise AssertionError("a run with worktrees off must not be asked for a target")

    assert resolve_execution_root(session, run, ticket, workspace, resolve_target=never) == repo
