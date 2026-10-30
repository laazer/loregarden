"""An integration branch that cannot take its base is found, surfaced, and survivable.

lg-durable-remote-335's tree: #560 changed `RunLogModal.tsx` on `main` after
the tree had changed it too. Nothing noticed until a chat turn on a ticket in
the tree tried to cut a worktree, and it answered "Baxter unavailable" with no
card, no block, and nothing else on record.
"""

from __future__ import annotations

import inspect
import subprocess
import threading
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services import (
    github_pr_service,
    integration_branch_health,
    integration_merge_agent,
)
from loregarden.services.github_pr_service import _refuse_tree_member, create_ticket_pull_request
from loregarden.services.integration_branch_health import check_integration_branches
from loregarden.services.integration_conflict_card import (
    conflict_card_title,
    report_integration_conflict,
)
from loregarden.services.integration_merge_budget import INTEGRATION_MERGE_STAGE
from loregarden.services.target_branch import (
    IntegrationConflictError,
    TargetBranchError,
    landing_target,
    resolve_target_branch,
    subtree_root,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, head_branch, make_repo

BRANCH = "integration/ms"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="health", name="health", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(autouse=True)
def _fresh_preview_cache():
    integration_branch_health._PREVIEWS.clear()
    integration_branch_health._IN_FLIGHT.clear()
    yield
    integration_branch_health._PREVIEWS.clear()
    integration_branch_health._IN_FLIGHT.clear()


def _ticket(session, workspace, external_id, *, parent=None, kind=WorkItemType.TASK):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=external_id,
        branch=f"loregarden/{external_id}",
        parent_ticket_id=parent.id if parent else None,
        state=TicketState.IN_PROGRESS,
        work_item_type=kind,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _without_branch(message: str, branch: str) -> str:
    """``message`` with the landing branch removed.

    The branch embeds the root's slug, so a bare substring test for the root's
    external id is satisfied by the branch name alone — it would pass for a
    refusal that never names the root. Removing the branch first makes the
    assertion about the root and nothing else.
    """
    assert branch in message
    return message.replace(branch, "")


def _initiative(session, external_id):
    """An initiative, which is cross-workspace (no `workspace_id`) and roots no
    landing tree of its own."""
    initiative = Ticket(
        external_id=external_id,
        workspace_id=None,
        title=external_id,
        branch="",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.INITIATIVE,
    )
    session.add(initiative)
    session.commit()
    session.refresh(initiative)
    return initiative


@pytest.fixture(name="tree")
def tree_fixture(session, workspace, repo):
    """A milestone `ms` with child `b`, its integration branch cut from main."""
    ms = _ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    child = _ticket(session, workspace, "b", parent=ms)
    assert resolve_target_branch(session, child, workspace, repo_root=repo) == BRANCH
    return ms, child


def _conflict(repo):
    commit_on(repo, BRANCH, "shared.txt", "integration side\n")
    commit_on(repo, "main", "shared.txt", "main side\n")


def _cards(session, root, status=ApprovalStatus.PENDING):
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == status,
            )
        ).all()
    )


# --- 1. detection when the base moves ---------------------------------------------


def test_the_sweep_finds_a_conflict_the_moment_main_moves(session, repo, tree):
    ms, _ = tree
    _conflict(repo)
    before = git(repo, "rev-parse", BRANCH).stdout.strip()

    report = check_integration_branches(session)

    assert [(r.branch, r.conflicted_files) for r in report] == [(BRANCH, ("shared.txt",))]
    [card] = _cards(session, ms)
    assert card.title == conflict_card_title(BRANCH, "main")
    assert "shared.txt" in card.impact
    assert git(repo, "rev-parse", BRANCH).stdout.strip() == before, "the sweep writes nothing"


def test_the_card_is_filed_once_and_closes_when_the_branch_takes_main(session, repo, tree):
    ms, _ = tree
    _conflict(repo)
    check_integration_branches(session)
    check_integration_branches(session)
    assert len(_cards(session, ms)) == 1

    # Someone resolves it: the integration side gives way.
    tree_dir = repo.parent / "resolve"
    git(repo, "worktree", "add", "-q", str(tree_dir), BRANCH)
    git(tree_dir, "merge", "-q", "-X", "theirs", "main", "-m", "resolve")

    check_integration_branches(session)

    assert _cards(session, ms) == []
    [closed] = _cards(session, ms, ApprovalStatus.APPROVED)
    assert closed.resolved_by == "automation"


def test_a_clean_tree_and_a_finished_tree_raise_nothing(session, repo, tree):
    ms, _ = tree
    commit_on(repo, "main", "other.txt", "unrelated\n")
    assert check_integration_branches(session)[0].conflicted_files == ()

    _conflict(repo)
    ms.state = TicketState.DONE
    session.add(ms)
    session.commit()
    assert check_integration_branches(session) == []
    assert _cards(session, ms) == []


def test_the_sweep_closes_the_card_once_main_holds_the_branch_squashed(session, repo, tree):
    """lg-durable-remote-335: the branch's commit reached main as a squash, then
    main edited the file it added. Nothing is missing from main, so the card the
    add/add conflict filed closes without anyone touching git."""
    ms, _ = tree
    commit_on(repo, BRANCH, "transport.py", "def detach():\n    pass\n")
    commit_on(repo, "main", "transport.py", "def detach():\n    pass\n")
    commit_on(repo, "main", "transport.py", "def detach():\n    return 1\n")
    report_integration_conflict(session, ms, branch=BRANCH, base="main", files=("transport.py",))
    before = git(repo, "rev-parse", BRANCH).stdout.strip()

    assert [r.conflicted_files for r in check_integration_branches(session)] == [()]

    assert _cards(session, ms) == []
    assert git(repo, "rev-parse", BRANCH).stdout.strip() == before, "the sweep writes nothing"


# --- 2 & 3. a cut that hits it files the card; chat survives, stages refuse -----------


def _run(session, workspace, ticket):
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="triage",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_stage_run_still_refuses_and_the_conflict_reaches_the_inbox(
    session, workspace, repo, tree
):
    ms, child = tree
    _conflict(repo)

    with pytest.raises(IntegrationConflictError, match="shared.txt"):
        resolve_execution_root(session, _run(session, workspace, child), child, workspace)

    assert len(_cards(session, ms)) == 1


def test_a_chat_turn_runs_on_the_stale_branch_instead_of_failing(session, workspace, repo, tree):
    ms, child = tree
    _conflict(repo)
    stale_tip = git(repo, "rev-parse", BRANCH).stdout.strip()

    root = resolve_execution_root(
        session, _run(session, workspace, child), child, workspace, stale_target_ok=True
    )

    assert root != repo
    assert head_branch(root) == child.branch
    assert git(root, "merge-base", "--is-ancestor", stale_tip, "HEAD").returncode == 0
    assert len(_cards(session, ms)) == 1


# --- 4. a tree member gets no PR of its own ----------------------------------------


def test_open_pr_refuses_a_ticket_that_ships_in_its_trees_pr(session, workspace, tree):
    ms, child = tree
    with pytest.raises(ValueError, match="integration/ms") as refusal:
        create_ticket_pull_request(session, child)

    # A branch name alone cannot be resolved back to a ticket
    # (`integration_branch_for` slugs a root with no external id to eight hex
    # characters), so the refusal has to name the root itself. The branch is
    # removed first because it embeds the root's slug, and an occurrence inside
    # it is not the root being named.
    assert ms.external_id in _without_branch(str(refusal.value), BRANCH)


def test_open_pr_refuses_a_grandchild_naming_its_trees_root_not_its_parent(session, workspace):
    """The refusal is keyed on the tree root, which for a grandchild is neither
    its parent nor itself — the one shape where "the root" and "the parent"
    differ for a ticket that is not a direct member. The ids here are distinct
    multi-word slugs so that neither name can match the message by accident.
    """
    root = _ticket(session, workspace, "ms-deep", kind=WorkItemType.MILESTONE)
    member = _ticket(session, workspace, "member-deep", parent=root)
    grandchild = _ticket(session, workspace, "leaf-deep", parent=member)

    with pytest.raises(ValueError) as refusal:
        _refuse_tree_member(session, grandchild, workspace)

    message = str(refusal.value)
    assert "integration/ms-deep" in message
    assert root.external_id in _without_branch(message, "integration/ms-deep")
    assert member.external_id not in message


def test_open_pr_lets_an_unroutable_landing_target_propagate(session, workspace):
    """C4: `TargetBranchError` out of the landing walk is not caught and
    re-dressed as a refusal. An initiative roots no tree, so it has no landing
    branch to report at all, and that is a different answer from "your work
    ships elsewhere"."""
    initiative = _initiative(session, "init-unroutable")

    with pytest.raises(TargetBranchError):
        _refuse_tree_member(session, initiative, workspace)


def test_a_milestone_under_an_initiative_is_its_own_root_despite_having_a_parent(
    session, workspace
):
    """`subtree_root` stops below an initiative, so a milestone under one is its
    own landing root — the base-branch case — even though `parent_ticket_id` is
    set. Only `root.id == ticket.id` tells this apart from `parent_ticket_id is
    None`; a check that used the latter would wrongly refuse this milestone a
    PR of its own."""
    initiative = _initiative(session, "init-cross")
    milestone = _ticket(
        session, workspace, "ms-under-init", parent=initiative, kind=WorkItemType.MILESTONE
    )

    # Without these two the test passes under either predicate: it would prove
    # the shape is handled, not that the shape is the divergent one.
    assert milestone.parent_ticket_id == initiative.id
    assert subtree_root(session, milestone).id == milestone.id

    _refuse_tree_member(session, milestone, workspace)


def test_the_refusal_reads_the_landing_walk_once_for_a_member_and_for_a_root(
    session, workspace, tree
):
    """One walk, which no assertion about the outcome can see.

    A body that asked twice — once for the branch, once for the root — raises
    the same message with the same two names in it, and returns silently for
    the same tickets, so every other test in this section is green for it. The
    call count is the only observable difference, so it is asserted directly.
    Both arms are counted: the early return is the arm where a second walk is
    easiest to reintroduce, because the value it returns is discarded.
    """
    # Control: the name this test patches is the one the function body reads.
    # A body calling `target_branch.landing_target` through the module would
    # leave the patch unused and the count at zero, which the asserts below
    # would read as "fewer walks", not as a test that measured nothing.
    assert github_pr_service.landing_target is landing_target

    ms, child = tree

    with mock.patch.object(github_pr_service, "landing_target", wraps=landing_target) as walk:
        with pytest.raises(ValueError):
            _refuse_tree_member(session, child, workspace)
    assert walk.call_count == 1, "a tree member's refusal walks the tree once"

    with mock.patch.object(github_pr_service, "landing_target", wraps=landing_target) as walk:
        _refuse_tree_member(session, ms, workspace)
    assert walk.call_count == 1, "the silent return for a root walks the tree once"


def test_the_pr_module_keeps_no_second_way_to_derive_where_a_ticket_lands(session):
    """The three-step spelling is gone from the module, not just from the one
    function body. `landing_target` answers all three questions, so a module
    that still reaches for `target_branch_name`, `resolve_orchestration_profile`
    or `subtree_root` has a second derivation of the same facts somewhere — the
    shape this change removed, and the shape it would come back as. Checked over
    the whole source so an attribute-style call (`target_branch.subtree_root`)
    counts as well as an import.
    """
    source = inspect.getsource(github_pr_service)

    assert "landing_target" in source
    for second_derivation in (
        "target_branch_name",
        "resolve_orchestration_profile",
        "subtree_root",
    ):
        assert second_derivation not in source, (
            f"{second_derivation} re-derives what landing_target already returned"
        )
        assert not hasattr(github_pr_service, second_derivation)


def test_a_refused_member_reaches_neither_git_nor_gh(session, workspace, tree):
    """The refusal comes before any side effect, which is the whole point of
    refusing: a member's branch pushed to `origin` is published whether or not
    the PR is then opened, and nothing in the refusal path undoes it. Ordering
    is invisible to the refusal's message, so the subprocess helpers are made
    to fail on contact.
    """
    _ms, child = tree

    def _forbidden(*args, **kwargs):
        raise AssertionError(f"the refusal must come first, before {args!r}")

    with (
        mock.patch.object(github_pr_service, "run_git", _forbidden),
        mock.patch.object(github_pr_service, "run_gh", _forbidden),
    ):
        with pytest.raises(ValueError, match="integration/ms"):
            create_ticket_pull_request(session, child)


# --- 5. the sweep gives a conflict the agent's merge turn ---------------------------
#
# lg-run-durability-314, 2026-10-12: two import lines on each side of one file,
# and the card waited for a person. Only a stage dispatch ran the merge turn, and
# the conflict had already stopped every dispatch in the tree.


def _keep_both_sides(*_args, tree, **_kwargs) -> str:
    (tree / "shared.txt").write_text("integration side\nmain side\n")
    git(tree, "add", "shared.txt")
    return "Kept both sides."


def _merge_runs(session, root):
    session.expire_all()
    return list(
        session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == root.id, AgentRun.stage_key == INTEGRATION_MERGE_STAGE
            )
        ).all()
    )


def _is_ancestor(repo, ancestor, descendant) -> bool:
    try:
        git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    except subprocess.CalledProcessError as exc:
        # Exit 1 is git's "no"; anything else is a broken fixture, not an answer.
        if exc.returncode != 1:
            raise
        return False
    return True


def test_an_open_conflict_card_gets_a_merge_turn_and_closes(session, repo, tree):
    ms, _ = tree
    _conflict(repo)
    report_integration_conflict(session, ms, branch=BRANCH, base="main", files=("shared.txt",))

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_keep_both_sides):
        check_integration_branches(session)

    assert [run.status for run in _merge_runs(session, ms)] == [RunStatus.SUCCEEDED]
    assert _is_ancestor(repo, "main", BRANCH)
    assert _cards(session, ms) == []


def test_a_turn_that_does_not_resolve_it_leaves_the_card_saying_why(session, repo, tree):
    ms, _ = tree
    _conflict(repo)

    check_integration_branches(session)  # conftest's stub turn resolves nothing

    [run] = _merge_runs(session, ms)
    assert run.status == RunStatus.FAILED
    [card] = _cards(session, ms)
    assert "left conflicts unresolved in: shared.txt" in card.impact


def test_with_the_switch_off_no_turn_runs_and_the_card_says_so(session, repo, tree):
    ms, _ = tree
    ms.git_automation_json = '{"auto_resolve_conflicts": false}'
    session.add(ms)
    session.commit()
    _conflict(repo)

    check_integration_branches(session)

    assert _merge_runs(session, ms) == []
    [card] = _cards(session, ms)
    assert "auto_resolve_conflicts is off" in card.impact


def test_the_sweep_stops_at_the_attempt_budget_for_the_tip(session, repo, tree):
    ms, _ = tree
    _conflict(repo)

    for _ in range(4):
        check_integration_branches(session)

    # The default `max_conflict_resolve_attempts` is 2, per integration tip.
    assert len(_merge_runs(session, ms)) == 2
    [card] = _cards(session, ms)
    assert "2 automatic merge attempt(s)" in card.impact


def test_the_turn_runs_off_the_sweep_and_only_once_per_branch(session, repo, tree, monkeypatch):
    ms, _ = tree
    _conflict(repo)
    monkeypatch.delenv("LOREGARDEN_SYNC_RUNS")
    started, release = threading.Event(), threading.Event()

    def held_turn(*args, tree, **kwargs) -> str:
        started.set()
        assert release.wait(30), "the test never released the turn"
        return _keep_both_sides(*args, tree=tree, **kwargs)

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=held_turn) as turn:
        check_integration_branches(session)  # returns while the turn is held
        assert started.wait(30), "the turn never started"
        check_integration_branches(session)  # the branch's turn is still in flight
        [running] = _merge_runs(session, ms)
        assert running.status == RunStatus.RUNNING
        # Not a second thread queued behind the first on the merge lock, one per tick.
        merging = [
            thread
            for thread in threading.enumerate()
            if thread.name == f"loregarden-integration-merge-{BRANCH}"
        ]
        assert len(merging) == 1

        release.set()
        merging[0].join(30)

    assert turn.call_count == 1
    assert [run.status for run in _merge_runs(session, ms)] == [RunStatus.SUCCEEDED]
    assert _is_ancestor(repo, "main", BRANCH)
    assert integration_branch_health._IN_FLIGHT == set()


def test_a_turn_that_crashes_is_recorded_on_the_card(session, repo, tree):
    ms, _ = tree
    _conflict(repo)

    with mock.patch.object(
        integration_branch_health, "merge_base_by_agent", side_effect=RuntimeError("boom")
    ):
        check_integration_branches(session)

    [card] = _cards(session, ms)
    assert "the merge turn crashed: boom" in card.impact
    assert integration_branch_health._IN_FLIGHT == set()
