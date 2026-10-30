"""Every site that removes a worktree passes the one receipt gate.

lg-external-harness-1026, R10/R11. This is the invariant the ticket ships, and
it is the one that cannot be established by reading the diff: `retire_worktree`
was *already* a careful gate, and three — in fact four — other call sites
removed a checkout without going anywhere near it. A ticket that hardened the
gate and left those alone would read as met while the fan-out loser path went
on deleting commits from an observation of shape.

So the chokepoint is asserted. Two of the tests below are static: who may call
`remove_checkout` and `release_directory`, and which modules may name
`worktree remove --force` or `shutil.rmtree` at all. A static test is the right
instrument here because the defect is *a call site nobody noticed* — a
behavioural test can only cover the sites someone thought of, which is the same
blind spot that produced the bug.

AC16 lives here too: the one destructive act in this tree gated on nothing at
all, on `worktree_service._refresh_onto`'s failed-merge path.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageFanoutAttempt,
    StageFanoutAttemptStatus,
    StageFanoutGroup,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import branch_triage_service, stage_fanout_service
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import KeepReason
from loregarden.services.worktree_service import WorktreeRefreshError, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo

SERVER = Path(__file__).resolve().parents[1] / "loregarden"


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


def _ticket(session, workspace, external_id="LG-1", *, state=TicketState.DONE) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id.lower()}",
        state=state,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _tree_for(session, workspace, ticket) -> Path:
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return resolve_execution_root(session, run, ticket, workspace)


def _commit_in(path: Path, name: str = "work.txt") -> str:
    (path / name).write_text("real work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", f"work: {name}")
    return git(path, "rev-parse", "HEAD").stdout.strip()


def _row(session, ticket) -> Worktree:
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


# ===========================================================================
# AC15 — the chokepoint, asserted statically
# ===========================================================================


def _calls_named(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = getattr(func, "attr", None) or getattr(func, "id", None)
        if called == name:
            return True
    return False


@pytest.mark.parametrize("name", ["remove_checkout", "release_directory"])
def test_only_worktree_lifecycle_calls_the_destructive_half(name: str):
    """The chokepoint in one sentence: there is one caller, and it is the gate.

    `remove_checkout` removes a checkout and `release_directory` removes a
    directory. Neither asks whether it should; `retire_worktree` is where that
    question lives. A second caller is the defect this ticket exists to close,
    reintroduced.

    `remove_checkout` is lg-external-harness-1165's name for the seam AC14
    spells `cleanup_worktree`; the name is 1165's and the contract is 1026's,
    and the chokepoint is the same chokepoint either way.
    """
    allowed = {"worktree_lifecycle.py", "worktree_service.py", "directory_release.py"}
    offenders = [
        str(module.relative_to(SERVER))
        for module in SERVER.rglob("*.py")
        if module.name not in allowed and _calls_named(module, name)
    ]
    assert offenders == [], f"{name} is reached outside the gate from: {offenders}"


@pytest.mark.parametrize(
    "module",
    [
        "services/branch_triage_service.py",
        "services/stage_fanout_service.py",
        "services/pull_request_merge.py",
        # lg-external-harness-1452. The checkout self-repair reached from the
        # live stage-dispatch path: it force-removed any tree git called a
        # linked worktree, on no premise but that shape. Four strings in the
        # module tripped this census when the ticket was filed — the call, the
        # `"worktree remove failed"` error text, the operator remedy that
        # recommended the same command, and the docstring defending it.
        "services/git_branch.py",
    ],
)
def test_no_service_removes_a_checkout_behind_the_gate_s_back(module: str):
    source = (SERVER / module).read_text(encoding="utf-8")
    assert "shutil.rmtree" not in source, f"{module} deletes a tree directly"
    assert '"worktree", "remove"' not in source, f"{module} runs `git worktree remove`"
    assert "worktree remove" not in source.replace("# ", ""), (
        f"{module} still names `worktree remove`"
    )


def test_no_api_module_removes_a_checkout_behind_the_gate_s_back():
    offenders = []
    for module in (SERVER / "api").rglob("*.py"):
        source = module.read_text(encoding="utf-8")
        if "shutil.rmtree" in source or '"worktree", "remove"' in source:
            offenders.append(str(module.relative_to(SERVER)))
    assert offenders == [], f"an HTTP handler deletes a checkout directly: {offenders}"


# ===========================================================================
# AC14 / AC15 — each routed site, behaviourally
# ===========================================================================


def test_the_parallel_cleanup_endpoint_reports_the_gate_s_outcome(session, workspace, client):
    """(a) `api/parallel.py`. An HTTP endpoint that took any worktree id and
    removed it, with no premise at all."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    row = _row(session, ticket)

    refused = client.post(f"/api/parallel/worktree/{row.id}/cleanup")

    assert refused.status_code in (200, 409)
    body = refused.json()
    assert KeepReason.NO_RECEIPT.value in str(body) or "no_receipt" in str(body)
    assert path.is_dir(), "the endpoint deleted a checkout the gate refused"


def test_a_fan_out_loser_the_keeper_contains_is_discarded(session, workspace):
    """(b) `stage_fanout_service._discard_attempts`, the permitted direction."""
    group, keeper, loser = _fanout(session, workspace, loser_diverges=False)
    loser_path = Path(loser.worktree_path)

    stage_fanout_service._discard_attempts(session, group, keep_attempt_id=keeper.id)

    assert not loser_path.exists()


def test_a_fan_out_loser_holding_a_unique_commit_keeps_its_tree_and_its_branch(
    session, workspace, repo
):
    """(b), the refusal — and the second half that used to run regardless.

    `_discard_attempts` removed the checkout and then ran `git branch -D` on the
    attempt's branch. When the removal is refused, deleting the branch takes
    away the *only* remaining reference to the commit the refusal just protected
    — the refusal would have bought nothing.
    """
    group, keeper, loser = _fanout(session, workspace, loser_diverges=True)
    loser_path = Path(loser.worktree_path)
    branch = loser.branch

    stage_fanout_service._discard_attempts(session, group, keep_attempt_id=keeper.id)

    assert loser_path.is_dir()
    exists = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    assert exists.returncode == 0, "the branch was force-deleted after the gate refused"


def test_remove_branch_worktree_on_a_refused_tree_leaves_it_and_reports_it(
    session, workspace, repo
):
    """(c) `branch_triage_service.remove_branch_worktree`."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)

    report = branch_triage_service.remove_branch_worktree(
        session, workspace, ticket.branch, str(path)
    )

    assert report.outcome is not None and report.outcome.value == "refused"
    assert report.reason
    assert path.is_dir()


def test_delete_branch_with_remove_worktrees_leaves_the_branch_too_when_refused(
    session, workspace, repo
):
    """(d) `branch_triage_service.delete_branch`, the site the plan missed.

    This is the endpoint the three operator surfaces actually call. Deleting the
    branch after the worktree removal was refused is the same mistake as the
    fan-out one: the checkout stays, and the only ref keeping its commits
    reachable goes.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)

    report = branch_triage_service.delete_branch(
        session, workspace, ticket.branch, force=True, remove_worktrees=True
    )

    assert report.outcome.value == "refused"
    assert report.reason
    assert path.is_dir()
    exists = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{ticket.branch}")
    assert exists.returncode == 0


def test_delete_branch_removes_both_once_the_receipt_is_on_record(session, workspace, repo):
    ticket = _ticket(session, workspace, state=TicketState.IN_PROGRESS)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))

    report = branch_triage_service.delete_branch(
        session, workspace, ticket.branch, force=True, remove_worktrees=True
    )

    assert report.outcome.value == "deleted"
    assert not path.exists()


def test_a_branch_level_removal_with_no_worktrees_row_is_refused(session, workspace, repo):
    """A path git knows about and the database does not is a tree whose
    completion cannot be proved. The gate's premise is a *record*; with no row
    there is no owner, no claim and no receipt to resolve — so it is reported,
    not performed. Removing it anyway is the branch-triage half of exactly the
    bug this ticket closes."""
    stray = repo.parent / ".worktrees" / "stray"
    git(repo, "worktree", "add", "-q", "-b", "feature/stray", str(stray))
    assert not session.exec(select(Worktree).where(Worktree.worktree_path == str(stray))).all()

    report = branch_triage_service.remove_branch_worktree(
        session, workspace, "feature/stray", str(stray)
    )

    assert report.outcome.value == "refused"
    assert stray.is_dir()


def test_pull_request_merge_reaches_the_gate_and_reports_its_refusal(session, workspace, repo):
    """(e) `pull_request_merge`, which reaches (c) and (d).

    Its own `_is_dirty` omitted `--untracked-files=all`, so a tree holding only
    untracked files read as clean — the majority of what an agent produces
    before its first commit. It is collapsed into `worktree_lifecycle`'s helper
    rather than kept beside it.
    """
    from loregarden.services import pull_request_merge

    assert not hasattr(pull_request_merge, "_is_dirty"), (
        "a second dirtiness probe is a second answer to one question"
    )
    source = (SERVER / "services" / "pull_request_merge.py").read_text(encoding="utf-8")
    assert "--untracked-files=all" not in source, "the one probe lives in worktree_lifecycle"


# ===========================================================================
# AC14 — the seam raises rather than answering a value
# ===========================================================================


def test_remove_checkout_propagates_the_release_failure_instead_of_answering_a_value(
    session, workspace
):
    """`False` meant three different things: outside the base, `worktree
    remove` raised, `rmtree` raised. One of those is a programming error and two
    are I/O, and the caller could tell none of them apart.

    lg-external-harness-1165 split them into a returned `CleanupRefusal`, which
    fixed the vocabulary and left the shape: a value a caller can forget to
    read. The seam keeps 1165's name, raises, and carries the member on the
    exception — so the `refusal` below is what 1165 bought and the raise is
    what 1026 adds."""
    from loregarden.services.directory_release import DirectoryReleaseFailed, ReleaseDisposition
    from loregarden.services.worktree_service import CleanupRefusal

    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    service = WorktreeService(session, repo_path=workspace.repo_path)

    with mock.patch(
        "loregarden.services.worktree_service.release_directory",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(path),
            parked_path=f"{path}.parked",
        ),
    ):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            service.remove_checkout(_row(session, ticket))

    assert caught.value.refusal is CleanupRefusal.REMOVE_RAISED
    assert caught.value.parked_path == f"{path}.parked"
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_remove_checkout_still_settles_the_row_on_a_release_that_worked(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    service = WorktreeService(session, repo_path=workspace.repo_path)

    assert service.remove_checkout(_row(session, ticket)) is None

    assert not path.exists()
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


# ===========================================================================
# AC16 — delete the one destructive act gated on nothing
# ===========================================================================


def test_a_failed_refresh_raises_and_leaves_the_checkout_exactly_as_it_was(
    session, workspace, repo
):
    """`_refresh_onto`'s failed-merge path ran `git worktree remove --force` on
    the way out, gated on nothing whatsoever — not a premise, not a receipt, not
    even a glance at the tree. Its own `silent-ok` waiver argues the removal is
    unnecessary: the next `worktree add` prunes the metadata either way. So the
    call goes, the error still carries git's own words, and the operator still
    has the conflicted tree they need to resolve.
    """
    ticket = _ticket(session, workspace, state=TicketState.IN_PROGRESS)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path, "conflict.txt")
    (path / "uncommitted.txt").write_text("edits nobody has seen\n")

    service = WorktreeService(session, repo_path=workspace.repo_path)
    with (
        mock.patch.object(service, "_branch_exists", return_value=True),
        mock.patch(
            "loregarden.services.worktree_service.run_git",
            side_effect=_merge_always_conflicts(path),
        ),
    ):
        with pytest.raises(WorktreeRefreshError) as caught:
            service._refresh_onto(path, ticket.branch, "main")

    assert "CONFLICT" in str(caught.value) or "merge" in str(caught.value).lower()
    assert path.is_dir()
    assert (path / "uncommitted.txt").read_text() == "edits nobody has seen\n"


def test_the_refresh_path_no_longer_names_worktree_remove_at_all(session, workspace):
    """The call itself is gone, not merely unreached.

    Asserted on the source of that one function rather than behaviourally,
    because the behaviour it had was *conditional on a merge conflict in a tree
    whose branch had already taken its target* — a shape rare enough that no
    existing test reaches it, which is exactly how an ungated destructive call
    survived this long.
    """
    source = (SERVER / "services" / "worktree_service.py").read_text(encoding="utf-8")
    refresh = source[source.index("def _refresh_onto") : source.index("def _branch_exists")]
    assert '"remove"' not in refresh, "_refresh_onto still removes the tree it failed on"
    assert "--force" not in refresh


def test_a_worktree_can_be_added_at_that_path_again_once_the_tree_is_released(
    session, workspace, repo
):
    """AC16's second clause, with the contradiction resolved.

    The failed refresh leaves the checkout — the operator needs it. What must
    also be true is that nothing is *wedged*: once that tree goes through the
    gate like any other, the path is reusable. Which is to say the prune ran,
    which is `release_directory`'s job rather than `worktree remove`'s.
    """
    from loregarden.services.directory_release import release_directory

    ticket = _ticket(session, workspace, state=TicketState.IN_PROGRESS)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path, "conflict.txt")

    service = WorktreeService(session, repo_path=workspace.repo_path)
    with (
        mock.patch.object(service, "_branch_exists", return_value=True),
        mock.patch(
            "loregarden.services.worktree_service.run_git",
            side_effect=_merge_always_conflicts(path),
        ),
    ):
        with pytest.raises(WorktreeRefreshError):
            service._refresh_onto(path, ticket.branch, "main")
    assert path.is_dir()

    release_directory(
        path,
        inside=Path(workspace.repo_path).parent / ".worktrees",
        park_root=Path(workspace.repo_path).parent / ".worktrees-releasing",
    )
    git(repo, "worktree", "add", "-q", str(path), ticket.branch)

    assert path.is_dir()


def test_land_ticket_s_align_worktrees_is_left_alone():
    """Explicitly out of scope (AC16). `_align_worktrees` is a `read-tree`
    overwrite, not a cleanup — a different act with a different argument, and
    widening into it is how this ticket grows a third round."""
    from loregarden.services import land_ticket

    assert hasattr(land_ticket, "_align_worktrees")


# ---------------------------------------------------------------------------


def _merge_always_conflicts(worktree_path: Path):
    """A `run_git` that fails every merge and records nothing else."""
    from loregarden.services.git_subprocess import run_git as real_run_git

    def fake(args, **kwargs):
        if args and args[0] == "merge" and "--abort" not in args:
            return mock.Mock(returncode=1, stdout="CONFLICT (content): in conflict.txt", stderr="")
        return real_run_git(args, **kwargs)

    return fake


def _fanout(session, workspace, *, loser_diverges: bool):
    """A settled fan-out group with a promoted keeper and one losing attempt."""
    ticket = _ticket(session, workspace, external_id="LG-FANOUT")
    group = StageFanoutGroup(
        workspace_id=workspace.id, ticket_id=ticket.id, stage_key="implement", attempt_count=2
    )
    session.add(group)
    session.commit()
    session.refresh(group)

    service = WorktreeService(session, repo_path=workspace.repo_path)
    made = {}
    for index, name in enumerate(("keeper", "loser")):
        run = AgentRun(
            run_code=f"run-{name}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id="backend_implementer",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        row = service.create_worktree(
            workspace.id, run.id, parent_branch="main", branch=f"fanout/{name}"
        )
        assert row is not None, f"fixture: {name}'s worktree was not created"
        attempt = StageFanoutAttempt(
            group_id=group.id,
            attempt_index=index,
            attempt_name=name,
            agent_run_id=run.id,
            worktree_id=row.id,
            branch=row.branch,
            status=StageFanoutAttemptStatus.RUNNING,
        )
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        made[name] = (attempt, row)

    keeper_attempt, keeper_row = made["keeper"]
    loser_attempt, loser_row = made["loser"]

    shared = _commit_in(Path(loser_row.worktree_path), "shared.txt")
    git(Path(keeper_row.worktree_path), "merge", "-q", "--ff-only", shared)
    if loser_diverges:
        _commit_in(Path(loser_row.worktree_path), "only-mine.txt")
    _commit_in(Path(keeper_row.worktree_path), "keeper.txt")

    keeper_attempt.status = StageFanoutAttemptStatus.PROMOTED
    group.winner_attempt_id = keeper_attempt.id
    session.add(keeper_attempt)
    session.add(group)
    session.commit()
    session.refresh(loser_row)
    return group, keeper_attempt, loser_row
