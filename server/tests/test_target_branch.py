"""Where a ticket's work lands: the integration branch per subtree (767)."""

import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    Ticket,
    WorkItemType,
    Workspace,
)
from loregarden.services import integration_merge_agent
from loregarden.services.git_subprocess import scrubbed_git_env
from loregarden.services.hierarchy_service import reparent_ticket
from loregarden.services.integration_conflict_card import report_integration_conflict
from loregarden.services.integration_merge_budget import INTEGRATION_MERGE_STAGE
from loregarden.services.target_branch import (
    IntegrationConflictError,
    TargetBranchError,
    ensure_integration_branch,
    integration_branch_for,
    landing_target,
    resolve_target_branch,
    subtree_root,
    target_branch_name,
)
from sqlmodel import Session, select


def _git(args: list[str], *, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    _git(["add", "."], cwd=path)
    _git(["commit", "-m", "init"], cwd=path)
    return path


@pytest.fixture
def workspace(db_session: Session, repo: Path) -> Workspace:
    # No profile file matches this slug, so the profile falls back to the
    # default and `git.base_branch` is "main".
    ws = Workspace(id="ws-target", slug="target-branch-test", name="Target", repo_path=str(repo))
    db_session.add(ws)
    db_session.commit()
    return ws


def _ticket(
    db_session: Session,
    workspace: Workspace,
    external_id: str,
    *,
    parent: Ticket | None = None,
    work_item_type: WorkItemType = WorkItemType.TASK,
) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        title=external_id,
        workspace_id=workspace.id,
        work_item_type=work_item_type,
        parent_ticket_id=parent.id if parent else None,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _branches(repo: Path) -> set[str]:
    return set(_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=repo).split())


def test_subtree_root_walks_to_the_topmost_ancestor(db_session: Session, workspace: Workspace):
    milestone = _ticket(db_session, workspace, "ms-1", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(db_session, workspace, "ft-1", parent=milestone)
    task = _ticket(db_session, workspace, "tk-1", parent=feature)

    assert subtree_root(db_session, task).id == milestone.id
    assert subtree_root(db_session, feature).id == milestone.id
    assert subtree_root(db_session, milestone).id == milestone.id


def test_a_top_level_ticket_targets_the_base_branch(db_session: Session, workspace: Workspace):
    solo = _ticket(db_session, workspace, "solo-1")
    assert target_branch_name(db_session, solo, workspace) == "main"


def test_the_root_of_a_tree_targets_the_base_branch(db_session: Session, workspace: Workspace):
    """The tree goes to main once, as a whole — its root is what lands there."""
    milestone = _ticket(db_session, workspace, "ms-2", work_item_type=WorkItemType.MILESTONE)
    _ticket(db_session, workspace, "tk-2", parent=milestone)
    assert target_branch_name(db_session, milestone, workspace) == "main"


def test_every_descendant_shares_the_root_integration_branch(
    db_session: Session, workspace: Workspace
):
    milestone = _ticket(db_session, workspace, "lg-ms-3", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(db_session, workspace, "lg-ft-3", parent=milestone)
    task = _ticket(db_session, workspace, "lg-tk-3", parent=feature)

    assert integration_branch_for(milestone) == "integration/lg-ms-3"
    assert target_branch_name(db_session, feature, workspace) == "integration/lg-ms-3"
    assert target_branch_name(db_session, task, workspace) == "integration/lg-ms-3"


# --- one walk, two projections (lg-verdict-outcome-1655) ----------------------------
#
# `target_branch_name` answered half of "where does this ticket's work land": the
# branch, never the root ticket that keys it. Its caller in
# `ticket_pull_request._ships_with` had loaded that root through `subtree_root`
# and thrown it away, so the PR tab could name the tree's branch and not the
# ticket it belongs to. `landing_target` exposes the same single walk at both
# widths; `target_branch_name` is its first projection.


def test_landing_target_names_the_branch_and_the_root_that_keys_it(
    db_session: Session, workspace: Workspace
):
    """AC1/AC4, all four shapes.

    `root` is None exactly when the ticket is its own root, which is also the
    only case where `branch` is the workspace base branch. For a member it is
    the root `Ticket` row itself — never the ticket passed in, and never the
    nearest parent, which is why the grandchild is here beside the child.
    """
    solo = _ticket(db_session, workspace, "solo-20")
    milestone = _ticket(db_session, workspace, "lg-ms-20", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(db_session, workspace, "lg-ft-20", parent=milestone)
    task = _ticket(db_session, workspace, "lg-tk-20", parent=feature)

    # Its own root: no root named, and the base branch.
    for own_root in (solo, milestone):
        target = landing_target(db_session, own_root, workspace)
        assert target.root is None, own_root.external_id
        assert target.branch == "main", own_root.external_id

    # A member and a grandchild: the tree's root, and the tree's branch.
    for member in (feature, task):
        target = landing_target(db_session, member, workspace)
        assert target.root is not None, member.external_id
        assert target.root.id == milestone.id, member.external_id
        assert target.root.id != member.id, member.external_id
        assert target.root.external_id == "lg-ms-20", member.external_id
        assert target.branch == "integration/lg-ms-20", member.external_id

    # The two projections are one derivation: `.branch` cannot disagree with the
    # string function, in any of the four shapes.
    for ticket in (solo, milestone, feature, task):
        assert landing_target(db_session, ticket, workspace).branch == target_branch_name(
            db_session, ticket, workspace
        ), ticket.external_id


def test_landing_target_raises_wherever_target_branch_name_raises(
    db_session: Session, workspace: Workspace
):
    """AC1: raising behaviour is unchanged, which only holds if the error
    escapes the function the delegate now calls. An initiative is refused, and
    so is a type the hierarchy forbids directly under one."""
    initiative = _initiative(db_session, "init-x-20")
    stray = _ticket(db_session, workspace, "lg-tk-21", parent=initiative)

    with pytest.raises(TargetBranchError, match="init-x-20"):
        landing_target(db_session, initiative, workspace)
    with pytest.raises(TargetBranchError, match="lg-tk-21"):
        landing_target(db_session, stray, workspace)


def test_landing_target_cannot_be_re_pointed_after_it_is_answered(
    db_session: Session, workspace: Workspace
):
    """AC1: the model is frozen, whichever of the two allowed shapes it is.

    One walk answered at two widths is only one answer while nothing can edit
    half of it: a caller that re-pointed `.branch` would leave `.root` naming a
    tree the branch no longer belongs to, which is the disagreement this seam
    exists to make impossible. A Pydantic model raises `ValidationError` and a
    frozen dataclass `FrozenInstanceError`, so the assertion is on the refusal
    and on the value surviving it, never on the exception's type.
    """
    milestone = _ticket(db_session, workspace, "lg-ms-22", work_item_type=WorkItemType.MILESTONE)
    member = _ticket(db_session, workspace, "lg-tk-22", parent=milestone)
    target = landing_target(db_session, member, workspace)

    for field, value in (("branch", "main"), ("root", None)):
        with pytest.raises(Exception):
            setattr(target, field, value)

    # Both fields are still what the walk answered, which is also what proves
    # the refusals above were the model refusing and not a typo in a name.
    assert target.branch == "integration/lg-ms-22"
    assert target.root is not None and target.root.id == milestone.id


def test_a_milestone_under_an_initiative_names_no_root_either(
    db_session: Session, workspace: Workspace
):
    """AC1: "its own root" is `root.id == ticket.id`, not "has no parent".

    `subtree_root` stops at an initiative, so a milestone filed under one is its
    own landing root while still carrying a `parent_ticket_id`. Deciding the
    `None` on the parent id instead would name this milestone as its own tree's
    root — a payload whose branch is the base branch and whose root ref is the
    ticket you are looking at, which is a self-link onto `main`.
    """
    initiative = _initiative(db_session, "init-x-22")
    milestone = _ticket(
        db_session, workspace, "lg-ms-23", parent=initiative, work_item_type=WorkItemType.MILESTONE
    )
    feature = _ticket(
        db_session, workspace, "lg-ft-23", parent=milestone, work_item_type=WorkItemType.FEATURE
    )

    own = landing_target(db_session, milestone, workspace)
    assert milestone.parent_ticket_id == initiative.id
    assert own.root is None
    assert own.branch == "main"

    inside = landing_target(db_session, feature, workspace)
    assert inside.root is not None and inside.root.id == milestone.id
    assert inside.branch == "integration/lg-ms-23"


def test_resolve_creates_the_integration_branch_from_base_once(
    db_session: Session, workspace: Workspace, repo: Path
):
    milestone = _ticket(db_session, workspace, "lg-ms-4", work_item_type=WorkItemType.MILESTONE)
    task = _ticket(db_session, workspace, "lg-tk-4", parent=milestone)
    main_sha = _git(["rev-parse", "main"], cwd=repo)

    first = resolve_target_branch(db_session, task, workspace, repo_root=repo)
    assert first == "integration/lg-ms-4"
    assert _git(["rev-parse", first], cwd=repo) == main_sha
    after_first = _branches(repo)

    # Move main. A second call must not re-create the branch (770 brings it up
    # to main with a merge, which is a different thing from resetting it).
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "later"], cwd=repo)
    later_sha = _git(["rev-parse", "main"], cwd=repo)

    second = resolve_target_branch(db_session, task, workspace, repo_root=repo)
    assert second == first
    assert _branches(repo) == after_first
    tip = _git(["rev-parse", second], cwd=repo)
    assert tip not in (main_sha, later_sha), "a merge, not a reset to either side"
    parents = _git(["rev-list", "--parents", "-n", "1", tip], cwd=repo).split()[1:]
    assert set(parents) == {main_sha, later_sha}


def test_resolve_never_creates_the_base_branch(
    db_session: Session, workspace: Workspace, repo: Path
):
    solo = _ticket(db_session, workspace, "solo-5")
    before = _branches(repo)
    assert resolve_target_branch(db_session, solo, workspace, repo_root=repo) == "main"
    assert _branches(repo) == before


def test_creation_does_not_move_any_checkout(db_session: Session, workspace: Workspace, repo: Path):
    milestone = _ticket(db_session, workspace, "lg-ms-6", work_item_type=WorkItemType.MILESTONE)
    task = _ticket(db_session, workspace, "lg-tk-6", parent=milestone)
    _git(["checkout", "-q", "-b", "elsewhere"], cwd=repo)

    resolve_target_branch(db_session, task, workspace, repo_root=repo)

    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo) == "elsewhere"


def test_a_missing_base_is_an_error_not_an_empty_branch(repo: Path):
    with pytest.raises(TargetBranchError, match="does not exist"):
        ensure_integration_branch(repo, "integration/x", "no-such-base")
    assert "integration/x" not in _branches(repo)


# --- an initiative is never a landing root -----------------------------------------


def _initiative(db_session: Session, external_id: str) -> Ticket:
    """Initiatives span workspaces, so they carry none of their own."""
    initiative = Ticket(
        external_id=external_id,
        title=external_id,
        workspace_id=None,
        work_item_type=WorkItemType.INITIATIVE,
    )
    db_session.add(initiative)
    db_session.commit()
    db_session.refresh(initiative)
    return initiative


def test_a_milestone_under_an_initiative_keeps_its_own_integration_branch(
    db_session: Session, workspace: Workspace
):
    initiative = _initiative(db_session, "init-x-1")
    milestone = _ticket(
        db_session, workspace, "lg-ms-10", parent=initiative, work_item_type=WorkItemType.MILESTONE
    )
    feature = _ticket(
        db_session, workspace, "lg-ft-10", parent=milestone, work_item_type=WorkItemType.FEATURE
    )
    task = _ticket(db_session, workspace, "lg-tk-10", parent=feature)

    assert subtree_root(db_session, task).id == milestone.id
    assert target_branch_name(db_session, task, workspace) == "integration/lg-ms-10"
    assert target_branch_name(db_session, feature, workspace) == "integration/lg-ms-10"
    # The milestone is still the root, so its completion is what publishes.
    assert target_branch_name(db_session, milestone, workspace) == "main"


def test_two_milestones_under_one_initiative_land_on_separate_branches(
    db_session: Session, workspace: Workspace
):
    initiative = _initiative(db_session, "init-x-2")
    first = _ticket(
        db_session, workspace, "lg-ms-11", parent=initiative, work_item_type=WorkItemType.MILESTONE
    )
    second = _ticket(
        db_session, workspace, "lg-ms-12", parent=initiative, work_item_type=WorkItemType.MILESTONE
    )
    a = _ticket(
        db_session, workspace, "lg-ft-11", parent=first, work_item_type=WorkItemType.FEATURE
    )
    b = _ticket(
        db_session, workspace, "lg-ft-12", parent=second, work_item_type=WorkItemType.FEATURE
    )

    assert target_branch_name(db_session, a, workspace) == "integration/lg-ms-11"
    assert target_branch_name(db_session, b, workspace) == "integration/lg-ms-12"


def test_attaching_or_detaching_a_milestone_leaves_its_target_branch_alone(
    db_session: Session, workspace: Workspace
):
    initiative = _initiative(db_session, "init-x-3")
    milestone = _ticket(db_session, workspace, "lg-ms-13", work_item_type=WorkItemType.MILESTONE)
    feature = _ticket(
        db_session, workspace, "lg-ft-13", parent=milestone, work_item_type=WorkItemType.FEATURE
    )
    task = _ticket(db_session, workspace, "lg-tk-13", parent=feature)

    def targets() -> list[str]:
        return [target_branch_name(db_session, t, workspace) for t in (milestone, feature, task)]

    before = targets()
    assert before == ["main", "integration/lg-ms-13", "integration/lg-ms-13"]

    reparent_ticket(db_session, milestone, initiative.id)
    db_session.commit()
    assert targets() == before

    reparent_ticket(db_session, milestone, None)
    db_session.commit()
    assert targets() == before


def test_a_feature_filed_directly_under_an_initiative_roots_its_own_tree(
    db_session: Session, workspace: Workspace
):
    """The hierarchy allows a feature or bug straight under an initiative."""
    initiative = _initiative(db_session, "init-x-4")
    feature = _ticket(
        db_session, workspace, "lg-ft-14", parent=initiative, work_item_type=WorkItemType.FEATURE
    )
    capability = _ticket(
        db_session, workspace, "lg-cp-14", parent=feature, work_item_type=WorkItemType.CAPABILITY
    )

    assert target_branch_name(db_session, feature, workspace) == "main"
    assert target_branch_name(db_session, capability, workspace) == "integration/lg-ft-14"


def test_an_initiative_passed_in_directly_is_refused(db_session: Session, workspace: Workspace):
    initiative = _initiative(db_session, "init-x-5")

    with pytest.raises(TargetBranchError, match="init-x-5"):
        subtree_root(db_session, initiative)
    with pytest.raises(TargetBranchError, match="init-x-5"):
        integration_branch_for(initiative)
    with pytest.raises(TargetBranchError, match="init-x-5"):
        target_branch_name(db_session, initiative, workspace)


def test_a_type_the_hierarchy_forbids_under_an_initiative_is_refused(
    db_session: Session, workspace: Workspace
):
    """A task written straight under an initiative (bypassing the hierarchy
    checks) has no tree to land in; it must not borrow the initiative's."""
    initiative = _initiative(db_session, "init-x-6")
    stray = _ticket(db_session, workspace, "lg-tk-16", parent=initiative)

    with pytest.raises(TargetBranchError, match="lg-tk-16"):
        target_branch_name(db_session, stray, workspace)


def test_no_ticket_under_an_initiative_resolves_a_branch_named_after_it(
    db_session: Session, workspace: Workspace, repo: Path
):
    """Every legal shape under an initiative, resolved for real against git."""
    initiative = _initiative(db_session, "init-x-7")
    tickets: list[Ticket] = []
    for m in range(2):
        ms = _ticket(
            db_session,
            workspace,
            f"ms-7{m}",
            parent=initiative,
            work_item_type=WorkItemType.MILESTONE,
        )
        ft = _ticket(
            db_session, workspace, f"ft-7{m}", parent=ms, work_item_type=WorkItemType.FEATURE
        )
        cp = _ticket(
            db_session, workspace, f"cp-7{m}", parent=ft, work_item_type=WorkItemType.CAPABILITY
        )
        tk = _ticket(db_session, workspace, f"tk-7{m}", parent=cp)
        bug = _ticket(db_session, workspace, f"bg-7{m}", parent=ms, work_item_type=WorkItemType.BUG)
        tickets += [ms, ft, cp, tk, bug]
    direct_ft = _ticket(
        db_session, workspace, "ft-7d", parent=initiative, work_item_type=WorkItemType.FEATURE
    )
    direct_bug = _ticket(
        db_session, workspace, "bg-7d", parent=initiative, work_item_type=WorkItemType.BUG
    )
    direct_cp = _ticket(
        db_session, workspace, "cp-7d", parent=direct_ft, work_item_type=WorkItemType.CAPABILITY
    )
    tickets += [direct_ft, direct_bug, direct_cp]

    resolved = {
        t.external_id: resolve_target_branch(db_session, t, workspace, repo_root=repo)
        for t in tickets
    }

    assert all(initiative.external_id not in branch for branch in resolved.values()), resolved
    assert not any(initiative.external_id in b for b in _branches(repo))
    assert {b for b in _branches(repo) if b.startswith("integration/")} == {
        "integration/ms-70",
        "integration/ms-71",
        "integration/ft-7d",
    }


# --- a branch whose work reached the base through a squash merge ----------------------
#
# lg-durable-remote-335: e93c2c19 was squash-merged to main as 71eb856b, then
# main edited the file it added (#570). Both sides "added" it, so the refresh
# saw an add/add conflict and every stage in the tree refused to start.

SQUASHED = "integration/ms-sq"


def _commit(repo: Path, branch: str, files: dict[str, str], message: str) -> str:
    """Commit ``files`` on ``branch``: in ``repo`` when it holds ``branch`` (git
    refuses a second checkout), otherwise through a throwaway worktree."""
    checked_out = _git(["branch", "--show-current"], cwd=repo) == branch
    tree = repo if checked_out else repo.parent / f"wt-{message.replace(' ', '-')}"
    if not checked_out:
        _git(["worktree", "add", "-q", str(tree), branch], cwd=repo)
    for name, content in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(content, encoding="utf-8")
    _git(["add", "."], cwd=tree)
    _git(["commit", "-q", "-m", message], cwd=tree)
    if not checked_out:
        _git(["worktree", "remove", "--force", str(tree)], cwd=repo)
    return _git(["rev-parse", branch], cwd=repo)


def _pending_cards(db_session: Session, root: Ticket) -> list[Approval]:
    return list(
        db_session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
            )
        ).all()
    )


COVERAGE = "".join(f"line {n}\n" for n in range(1, 21))


@pytest.fixture
def squashed_tree(db_session: Session, workspace: Workspace, repo: Path):
    """Milestone ``ms-sq`` and child ``tk-sq`` in lg-durable-remote-335's shape.

    The branch's work is two commits; main holds it as one squash, after an
    unrelated edit to a file the branch also touched (#568's writeCoverage.ts
    line), and then edits the file the branch added (#570). Two commits squashed
    into one share no patch-id, so `git cherry` alone would miss this.
    """
    _commit(repo, "main", {"coverage.ts": COVERAGE}, "coverage")
    milestone = _ticket(db_session, workspace, "ms-sq", work_item_type=WorkItemType.MILESTONE)
    child = _ticket(db_session, workspace, "tk-sq", parent=milestone)
    assert resolve_target_branch(db_session, child, workspace, repo_root=repo) == SQUASHED
    detached = COVERAGE.replace("line 2\n", "line 2\ndetach\n")
    _commit(repo, SQUASHED, {"transport.py": "def detach():\n    pass\n"}, "feat detach")
    _commit(repo, SQUASHED, {"coverage.ts": detached}, "feat coverage")
    _commit(repo, "main", {"coverage.ts": COVERAGE + "merge\n"}, "unrelated coverage")
    _commit(
        repo,
        "main",
        {"transport.py": "def detach():\n    pass\n", "coverage.ts": detached + "merge\n"},
        "squash detach",
    )
    _commit(repo, "main", {"transport.py": "def detach():\n    return 1\n"}, "main edits")
    return milestone, child


def test_a_squash_merged_branch_moves_to_the_base_tip_without_a_card(
    db_session: Session, workspace: Workspace, repo: Path, squashed_tree
):
    milestone, child = squashed_tree
    stale = _git(["rev-parse", SQUASHED], cwd=repo)
    report_integration_conflict(
        db_session, milestone, branch=SQUASHED, base="main", files=("transport.py",)
    )

    assert resolve_target_branch(db_session, child, workspace, repo_root=repo) == SQUASHED

    assert _git(["rev-parse", SQUASHED], cwd=repo) == _git(["rev-parse", "main"], cwd=repo)
    assert _git(["rev-parse", SQUASHED], cwd=repo) != stale
    assert _pending_cards(db_session, milestone) == [], "the stale card closes"


def test_a_unique_conflicting_commit_still_raises_and_files_the_card(
    db_session: Session, workspace: Workspace, repo: Path
):
    milestone = _ticket(db_session, workspace, "ms-uq", work_item_type=WorkItemType.MILESTONE)
    child = _ticket(db_session, workspace, "tk-uq", parent=milestone)
    branch = resolve_target_branch(db_session, child, workspace, repo_root=repo)
    tip = _commit(repo, branch, {"shared.txt": "branch side\n"}, "branch side")
    _commit(repo, "main", {"shared.txt": "main side\n"}, "main side")

    with pytest.raises(IntegrationConflictError, match="shared.txt"):
        resolve_target_branch(db_session, child, workspace, repo_root=repo)

    assert _git(["rev-parse", branch], cwd=repo) == tip, "a branch with unique work is never reset"
    assert len(_pending_cards(db_session, milestone)) == 1


def test_a_squash_merged_branch_with_one_more_commit_is_not_reset(
    db_session: Session, workspace: Workspace, repo: Path, squashed_tree
):
    milestone, child = squashed_tree
    tip = _commit(repo, SQUASHED, {"extra.txt": "only on the branch\n"}, "unique work")

    with pytest.raises(IntegrationConflictError, match="transport.py"):
        resolve_target_branch(db_session, child, workspace, repo_root=repo)

    assert _git(["rev-parse", SQUASHED], cwd=repo) == tip
    assert len(_pending_cards(db_session, milestone)) == 1


# --- an integration branch the base conflicts with is merged by an agent -------------
#
# init-pre-tinkercg-3: #578/#583/#592 changed three files on `main` that the
# milestone's integration branch had also changed. Every ticket the autopilot
# dispatched in that tree blocked two seconds in, the autopilot paused itself,
# and the only way forward was a person merging by hand.

MERGED = "integration/ms-mg"


@pytest.fixture
def conflicted(db_session: Session, workspace: Workspace, repo: Path):
    """Milestone ``ms-mg`` whose integration branch and ``main`` both rewrote one line."""
    milestone = _ticket(db_session, workspace, "ms-mg", work_item_type=WorkItemType.MILESTONE)
    child = _ticket(db_session, workspace, "tk-mg", parent=milestone)
    assert resolve_target_branch(db_session, child, workspace, repo_root=repo) == MERGED
    tip = _commit(repo, MERGED, {"shared.txt": "branch side\n"}, "branch side")
    _commit(repo, "main", {"shared.txt": "main side\n"}, "main side")
    return milestone, child, tip


def _resolving(content: str = "branch side\nmain side\n"):
    """A merge turn that keeps both sides and commits."""

    def turn(*_args, tree: Path, **_kwargs) -> str:
        (tree / "shared.txt").write_text(content, encoding="utf-8")
        _git(["add", "shared.txt"], cwd=tree)
        _git(["commit", "--no-edit"], cwd=tree)
        return "Kept both sides."

    return turn


def _giving_up(*_args, **_kwargs) -> str:
    return "This needs a person: both sides rewrote the same line for different reasons."


def _merge_runs(db_session: Session, milestone: Ticket) -> list[AgentRun]:
    return list(
        db_session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == milestone.id, AgentRun.stage_key == INTEGRATION_MERGE_STAGE
            )
        ).all()
    )


def test_a_conflict_is_merged_by_the_agent_and_the_ticket_starts(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    milestone, child, tip = conflicted
    main_tip = _git(["rev-parse", "main"], cwd=repo)

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_resolving()):
        assert resolve_target_branch(db_session, child, workspace, repo_root=repo) == MERGED

    merged = _git(["rev-parse", MERGED], cwd=repo)
    _git(["merge-base", "--is-ancestor", tip, merged], cwd=repo)
    _git(["merge-base", "--is-ancestor", main_tip, merged], cwd=repo)
    assert _git(["show", f"{MERGED}:shared.txt"], cwd=repo) == "branch side\nmain side"
    assert _pending_cards(db_session, milestone) == []
    [run] = _merge_runs(db_session, milestone)
    assert (run.status, run.start_head_sha, run.stdout) == (
        RunStatus.SUCCEEDED,
        tip,
        "Kept both sides.",
    )
    assert len(_git(["worktree", "list"], cwd=repo).splitlines()) == 1, "the merge tree is removed"


def test_the_merge_turn_renews_its_lease_while_it_works(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    """A merge turn outlives the 10-minute agent-run lease; unrenewed, the sweep
    reaped one mid-turn as abandoned (954, 2026-10-16)."""
    milestone, child, _ = conflicted
    renewed: list[str] = []

    def renewing(*args, **kwargs):
        [run] = _merge_runs(db_session, milestone)
        renewed.append(run.id)
        # The renewer is a thread: waited for, not read once, so a slow first beat
        # under load is not mistaken for no renewer.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            db_session.expire_all()
            if db_session.get(AgentRun, run.id).last_seen_at is not None:
                break
            time.sleep(0.05)
        assert db_session.get(AgentRun, run.id).last_seen_at is not None, (
            "the lease was not renewed while the turn ran"
        )
        return _resolving()(*args, **kwargs)

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=renewing):
        assert resolve_target_branch(db_session, child, workspace, repo_root=repo) == MERGED

    assert len(renewed) == 1


def test_a_turn_that_leaves_the_merge_unfinished_moves_nothing_and_asks_a_person(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    milestone, child, tip = conflicted

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_giving_up):
        with pytest.raises(
            IntegrationConflictError, match="left conflicts unresolved in: shared.txt"
        ):
            resolve_target_branch(db_session, child, workspace, repo_root=repo)

    assert _git(["rev-parse", MERGED], cwd=repo) == tip
    [card] = _pending_cards(db_session, milestone)
    assert "An agent tried the merge first" in card.impact
    [run] = _merge_runs(db_session, milestone)
    assert run.status == RunStatus.FAILED
    assert "This needs a person" in run.stdout


def test_committed_conflict_markers_are_refused(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    _, child, tip = conflicted
    markers = "<<<<<<< HEAD\nbranch side\n=======\nmain side\n>>>>>>> main\n"

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_resolving(markers)):
        with pytest.raises(IntegrationConflictError, match="conflict markers were committed"):
            resolve_target_branch(db_session, child, workspace, repo_root=repo)

    assert _git(["rev-parse", MERGED], cwd=repo) == tip


def test_failed_turns_stop_at_the_attempt_cap_for_that_tip(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    milestone, child, _ = conflicted

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_giving_up) as turn:
        for _ in range(3):
            with pytest.raises(IntegrationConflictError):
                resolve_target_branch(db_session, child, workspace, repo_root=repo)
        # The default `max_conflict_resolve_attempts` is 2.
        assert turn.call_count == 2

        [card] = _pending_cards(db_session, milestone)
        assert "2 automatic merge attempt(s)" in card.impact

        # A new landing is a new tip, and a new budget.
        _commit(repo, MERGED, {"shared.txt": "branch side again\n"}, "branch again")
        with pytest.raises(IntegrationConflictError):
            resolve_target_branch(db_session, child, workspace, repo_root=repo)
        assert turn.call_count == 3


def test_the_switch_off_leaves_the_conflict_for_a_person(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    milestone, child, _ = conflicted
    milestone.git_automation_json = '{"auto_resolve_conflicts": false}'
    db_session.add(milestone)
    db_session.commit()

    with mock.patch.object(integration_merge_agent, "_run_agent") as turn:
        with pytest.raises(IntegrationConflictError, match="auto_resolve_conflicts is off"):
            resolve_target_branch(db_session, child, workspace, repo_root=repo)

    turn.assert_not_called()
    assert _merge_runs(db_session, milestone) == []


def test_a_caller_that_can_run_stale_skips_the_turn(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    _, child, _ = conflicted

    with mock.patch.object(integration_merge_agent, "_run_agent") as turn:
        with pytest.raises(IntegrationConflictError):
            resolve_target_branch(
                db_session, child, workspace, repo_root=repo, merge_conflicts=False
            )

    turn.assert_not_called()


def test_a_sibling_landing_during_the_turn_is_kept(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    """The branch moves by compare-and-swap: a landing made while the agent
    worked is never overwritten by the merge."""
    milestone, child, _ = conflicted
    resolve = _resolving()
    landed: list[str] = []

    def turn_with_a_landing(*args, tree: Path, **kwargs) -> str:
        sibling = repo.parent / "sibling"
        _git(["worktree", "add", "-q", str(sibling), MERGED], cwd=repo)
        (sibling / "sibling.txt").write_text("landed\n", encoding="utf-8")
        _git(["add", "."], cwd=sibling)
        _git(["commit", "-qm", "sibling landed"], cwd=sibling)
        _git(["worktree", "remove", "--force", str(sibling)], cwd=repo)
        landed.append(_git(["rev-parse", MERGED], cwd=repo))
        return resolve(*args, tree=tree, **kwargs)

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=turn_with_a_landing):
        with pytest.raises(IntegrationConflictError, match="moved while the merge ran"):
            resolve_target_branch(db_session, child, workspace, repo_root=repo)

    assert _git(["rev-parse", MERGED], cwd=repo) == landed[0]
    assert _merge_runs(db_session, milestone)[0].status == RunStatus.FAILED


# --- the merge commit is written by loregarden, and graded on what it adds ------------
#
# lg-milestone-that-491: the merge turn resolved three conflicts with 214 tests
# green, and `git commit` refused it: pre-commit graded every file `main` had
# changed, and `main` carried `enums.py` at 1501 lines against a 1500 cap.


def _staging(files: dict[str, str]):
    """A merge turn that resolves with ``files`` and stages them, without committing."""

    def turn(*_args, tree: Path, **_kwargs) -> str:
        for name, content in files.items():
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            (tree / name).write_text(content, encoding="utf-8")
        _git(["add", *files], cwd=tree)
        return "Staged both sides."

    return turn


def _refusing_hook(repo: Path) -> None:
    """A pre-commit hook that refuses everything, as an inherited finding would."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho refused by pre-commit >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


#: One line past `py_organization_check.MAX_FILE_LINES`.
OVER_THE_CAP = "".join(f"value_{n} = {n}\n" for n in range(1501))

#: A finding `py-organization` reports on any line it grades.
ISINSTANCE_FINDING = "def read(payload):\n    return isinstance(payload, dict)\n"


def test_a_staged_resolution_is_committed_without_pre_commit_and_lands(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    milestone, child, tip = conflicted
    main_tip = _commit(repo, "main", {"src/pkg/big.py": OVER_THE_CAP}, "main over the cap")
    _refusing_hook(repo)
    turn = _staging({"shared.txt": "branch side\nmain side\n"})

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=turn):
        assert resolve_target_branch(db_session, child, workspace, repo_root=repo) == MERGED

    merged = _git(["rev-parse", MERGED], cwd=repo)
    assert _git(["rev-list", "--parents", "-n1", merged], cwd=repo).split()[1:] == [tip, main_tip]
    assert _git(["log", "-1", "--format=%s", merged], cwd=repo) == f"Merge main into {MERGED}"
    assert _git(["show", f"{MERGED}:shared.txt"], cwd=repo) == "branch side\nmain side"
    assert _pending_cards(db_session, milestone) == []
    [run] = _merge_runs(db_session, milestone)
    assert run.status == RunStatus.SUCCEEDED


def test_a_finding_the_resolution_writes_is_refused_by_the_gates(
    db_session: Session, workspace: Workspace, repo: Path
):
    milestone = _ticket(db_session, workspace, "ms-gt", work_item_type=WorkItemType.MILESTONE)
    child = _ticket(db_session, workspace, "tk-gt", parent=milestone)
    branch = resolve_target_branch(db_session, child, workspace, repo_root=repo)
    tip = _commit(repo, branch, {"src/pkg/mod.py": "x = 'branch'\n"}, "branch side")
    _commit(repo, "main", {"src/pkg/mod.py": "x = 'main'\n"}, "main side")
    turn = _staging({"src/pkg/mod.py": "x = 'both'\n" + ISINSTANCE_FINDING})

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=turn):
        with pytest.raises(IntegrationConflictError, match="fails the workspace gates"):
            resolve_target_branch(db_session, child, workspace, repo_root=repo)

    assert _git(["rev-parse", branch], cwd=repo) == tip
    [run] = _merge_runs(db_session, milestone)
    assert run.status == RunStatus.FAILED
    assert "src/pkg/mod.py" in run.stderr
    assert "isinstance" in run.stderr


def test_a_retry_on_the_same_tip_is_told_why_the_last_turn_was_refused(
    db_session: Session, workspace: Workspace, repo: Path, conflicted
):
    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=_giving_up) as turn:
        for _ in range(2):
            with pytest.raises(IntegrationConflictError):
                resolve_target_branch(db_session, conflicted[1], workspace, repo_root=repo)

    first, second = (call.kwargs["refused"] for call in turn.call_args_list)
    assert first == ""
    assert "left conflicts unresolved in: shared.txt" in second
    brief = integration_merge_agent.merge_brief(
        conflicted[0],
        branch=MERGED,
        base="main",
        tips=("a" * 40, "b" * 40),
        files=(),
        refused=second,
    )
    assert "left conflicts unresolved in: shared.txt" in brief


@pytest.mark.parametrize(
    ("leftover", "problem"),
    [
        ({"notes.txt": "scratch\n"}, "left untracked files: notes.txt"),
        ({"README.md": "# edited, not staged\n"}, "left changes it did not stage in: README.md"),
    ],
)
def test_a_turn_that_leaves_work_out_of_the_index_is_refused(
    db_session: Session, workspace: Workspace, repo: Path, conflicted, leftover, problem
):
    _, child, tip = conflicted
    stage = _staging({"shared.txt": "branch side\nmain side\n"})

    def turn(*args, tree: Path, **kwargs) -> str:
        reply = stage(*args, tree=tree, **kwargs)
        for name, content in leftover.items():
            (tree / name).write_text(content, encoding="utf-8")
        return reply

    with mock.patch.object(integration_merge_agent, "_run_agent", side_effect=turn):
        with pytest.raises(IntegrationConflictError, match=problem):
            resolve_target_branch(db_session, child, workspace, repo_root=repo)

    assert _git(["rev-parse", MERGED], cwd=repo) == tip
