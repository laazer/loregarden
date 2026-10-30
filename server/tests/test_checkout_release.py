"""The one path -> row -> receipt-gate decision, as its own seam.

lg-external-harness-1452. `branch_triage_service._release_checkout` was the
only place that turned a filesystem path into a gate answer, and it was
private to a 1150-line operator-facing service — so the checkout self-repair
on the live stage-dispatch path, which needed exactly the same decision, ran
`git worktree remove --force` instead and destroyed uncommitted work.

Moving it to `services/checkout_release.py` is what gives both callers one
answer. These tests are about that seam's contract, not about branch triage:
the fields three operator surfaces read are unchanged, and a path the
`worktrees` table does not know about is REPORTED rather than performed.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    RemovalOutcome,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo


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


@pytest.fixture(name="stray")
def stray_fixture(repo: Path) -> Path:
    """A checkout git has registered and the `worktrees` table has never heard of.

    Measured on live 2026-10-23: 44 git-registered worktrees against 54 ACTIVE
    rows, with 11 registered paths holding no ACTIVE row at all. Eleven trees
    the gate refuses where the self-repair used to force-remove.
    """
    tree = repo.parent / ".worktrees" / "stray"
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", "feature/stray", str(tree))
    (tree / "draft.txt").write_text("nobody has seen this\n")
    return tree


def _receipted(session, workspace, repo: Path, branch: str = "loregarden/lg-1") -> Worktree:
    """A checkout with everything the gate asks for: a row, an owner, a landing."""
    ticket = Ticket(
        external_id="lg-1",
        workspace_id=workspace.id,
        title="Add the thing",
        branch=branch,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code="run-lg-1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    tree = repo.parent / ".worktrees" / "lg-1"
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, str(tree))
    (tree / "work.txt").write_text("real work\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "work")

    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=str(tree),
        branch=branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    grant_receipt(session, ticket, repo, branch=branch)
    return row


def test_a_path_with_no_active_row_is_refused_with_no_receipt(session, workspace, repo, stray):
    """AC2. The premise is a *record*, so with no record there is nothing to perform.

    There is no owner to resolve a receipt for, no claim to read and no row to
    write the refusal on. Removing it anyway is the defect this ticket closes.
    """
    from loregarden.services.checkout_release import release_checkout

    report = release_checkout(session, str(stray))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert report.path == str(stray)
    assert report.detail, "a refusal that says nothing cannot be acted on"
    assert stray.is_dir()
    assert (stray / "draft.txt").read_text() == "nobody has seen this\n"
    assert session.exec(select(Worktree)).all() == [], "a refusal writes no row"


def test_a_receipted_checkout_is_released_and_its_row_settled(session, workspace, repo):
    """The other side. A gate that refuses everything is not a gate."""
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)

    report = release_checkout(session, str(tree))

    assert report.outcome is RemovalOutcome.DELETED
    assert report.reason == ""
    assert not tree.exists()
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value


def test_uncommitted_work_is_refused_even_with_a_receipt_on_record(session, workspace, repo):
    """The premise the self-repair never asked: is there work in there?"""
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    (tree / "in-flight.txt").write_text("an agent was mid-stage here\n")

    report = release_checkout(session, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert tree.is_dir()
    assert (tree / "in-flight.txt").read_text() == "an agent was mid-stage here\n"


def test_branch_triage_and_the_self_repair_read_one_definition(session, workspace, repo, stray):
    """A move, not a redesign.

    `branch_triage_service` imports the names back and keeps calling them by
    the same name, because three operator surfaces read `WorktreeRemoval`'s
    fields and `as_payload`. Two modules holding two spellings of "no
    worktrees row" is how the next drift starts.
    """
    from loregarden.services import branch_triage_service, checkout_release

    assert branch_triage_service.WorktreeRemoval is checkout_release.WorktreeRemoval

    through_triage = branch_triage_service.remove_branch_worktree(
        session, workspace, "feature/stray", str(stray)
    )
    through_the_seam = checkout_release.release_checkout(session, str(stray))

    assert through_triage.outcome is through_the_seam.outcome
    assert through_triage.reason == through_the_seam.reason
    assert through_triage.detail == through_the_seam.detail


def test_the_payload_three_surfaces_read_is_unchanged(session, workspace, repo, stray):
    """`as_payload`'s keys are a wire contract; the move may not touch them."""
    from loregarden.services.checkout_release import release_checkout

    payload = release_checkout(session, str(stray)).as_payload("feature/stray")

    assert set(payload) == {
        "branch",
        "removed_path",
        "outcome",
        "reason",
        "detail",
        "parked_path",
    }
    assert payload["outcome"] == RemovalOutcome.REFUSED.value


# ---------------------------------------------------------------------------
# lg-external-harness-1452, test-break. The lookup key, adversarially.
#
# A1 states path equality with no normalization layer, measured: 0 of 184 live
# `worktrees.worktree_path` values differ from their own realpath. A2 states
# that a `locked_at` spelled some other way refuses, "which is the safe
# direction". Both are assumptions written in prose on the ticket, and neither
# had a test. The plausible follow-up change — normalize until a row matches —
# re-opens the hole from the other side, and would pass every test above.
# ---------------------------------------------------------------------------


def test_a_path_spelled_through_a_symlinked_parent_finds_no_row(session, workspace, repo):
    """A2. The row holds one spelling, and another spelling is not that row.

    Refusing is the safe direction: the tree stays, the operator is told, and
    the remedy still frees the branch (R7). Removing it would mean resolving a
    path until it matched *something* and then deleting what it matched.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    link = repo.parent / "linked-worktrees"
    link.symlink_to(repo.parent / ".worktrees")

    report = release_checkout(session, str(link / tree.name))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert tree.is_dir(), "the tree the row does speak for was removed under another name"


def test_a_trailing_separator_is_a_different_path_and_refuses(session, workspace, repo):
    """A1, at its boundary. `str(tree) + "/"` is not `str(tree)`.

    Pinned rather than assumed: a caller that joined a path and left the
    separator on gets a refusal, not somebody else's receipt.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)

    report = release_checkout(session, str(tree) + "/")

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert tree.is_dir()


def test_a_row_that_is_no_longer_active_speaks_for_nothing(session, workspace, repo):
    """The `state == ACTIVE` half of the lookup, which the design never breaks.

    A settled row is the control plane's record that it is *done* with that
    checkout. Reading it as a receipt would let a tree be released twice — the
    second time on the strength of the first release's own bookkeeping.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    row.state = WorktreeState.CLEANUP
    session.add(row)
    session.commit()

    report = release_checkout(session, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert tree.is_dir()


def test_a_receipt_for_one_tree_does_not_license_another_on_the_same_branch(
    session, workspace, repo, stray
):
    """The key is the path, not the branch.

    git lets only one checkout hold a branch, so the two trees here cannot be
    on one ref — but the `worktrees` row carries its own `branch` column, and
    a row recording `feature/stray` at some other path is a row that exists.
    A lookup that matched on that column would hand the stray tree a receipt
    granted for a different directory, which is the defect with one extra step.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    row.branch = "feature/stray"
    session.add(row)
    session.commit()

    report = release_checkout(session, str(stray))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert stray.is_dir()
    assert (stray / "draft.txt").read_text() == "nobody has seen this\n"
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE.value, "and the other row is untouched"


def test_an_ignored_file_nothing_regenerates_refuses_a_fully_receipted_tree(
    session, workspace, repo
):
    """The premise `git status --porcelain` cannot see.

    An `.env` is ignored, so every shape check calls the tree clean; it is
    also the file somebody keeps in a worktree and nowhere else. The receipt
    says the *branch's* work landed and says nothing about this. A gate that
    stopped asking would pass every other test in this module.
    """
    from loregarden.services.checkout_release import release_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)
    (tree / ".gitignore").write_text(".env\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "ignore .env")
    (tree / ".env").write_text("TOKEN=keep-me\n")

    report = release_checkout(session, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert ".env" in report.detail, "the operator learns which file is holding the branch"
    assert (tree / ".env").read_text() == "TOKEN=keep-me\n"


def test_asking_twice_about_a_refused_tree_answers_twice_the_same(session, workspace, repo, stray):
    """Determinism. A refusal writes nothing, so it has nothing to drift on."""
    from loregarden.services.checkout_release import release_checkout

    first = release_checkout(session, str(stray))
    second = release_checkout(session, str(stray))

    assert (first.outcome, first.reason, first.detail) == (
        second.outcome,
        second.reason,
        second.detail,
    )
    assert session.exec(select(Worktree)).all() == []
    assert stray.is_dir()


_WORKSPACE_GONE_CLAUSE = "the workspace that cut this checkout is no longer on record, so the worktrees folder cannot be named"
_OUTSIDE_BASE_SENTENCE = "outside the worktrees folder"


def _active_row_without_a_landing(
    session, workspace, repo: Path, *, tree: Path, branch: str, external_id: str
) -> Worktree:
    """An ACTIVE checkout the table knows, with no landing on the owner.

    AC4: the real premise is NO_RECEIPT. A successful DELETE (`reason==""`)
    would miss the lying OUTSIDE_BASE sentence this pin exists to catch.
    """
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Owner of {branch}",
        branch=branch,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code=f"run-{external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, str(tree))
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=str(tree),
        branch=branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_an_in_base_checkout_is_not_told_it_is_outside_the_worktrees_folder(session, tmp_path):
    """AC4. Nested-geometry checkout, in the owning workspace's worktrees base.

    ACTIVE row, no landing: the real premise is NO_RECEIPT. After `repo_root`
    is dropped, containment is judged from that row's workspace, so an in-base
    path cannot be told it is outside the worktrees folder. A DELETED outcome
    (`reason==""`) fails this test — the lying refusal would never be re-tested.
    """
    from loregarden.services.checkout_release import release_checkout

    main = make_repo(tmp_path, "loregarden")
    workspace = Workspace(slug="lg-1488-ac4", name="lg-1488-ac4", repo_path=str(main))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    tree = tmp_path / ".worktrees" / "unreceipted"
    _active_row_without_a_landing(
        session,
        workspace,
        main,
        tree=tree,
        branch="loregarden/unreceipted",
        external_id="lg-1488-ac4",
    )

    report = release_checkout(session, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "no_receipt"
    assert report.reason not in {"", "cleanup_failed"}
    assert _OUTSIDE_BASE_SENTENCE not in report.detail
    assert tree.is_dir()


def test_retire_checkout_refuses_when_the_rows_workspace_is_gone(session, workspace, repo):
    """AC1. A receipted row whose workspace is no longer on record.

    `_service_for` returns None; the gate refuses with WORKSPACE_GONE, records
    `unresolved_reason`, and deletes nothing. Not UNREADABLE, not CLEANUP_FAILED,
    not the OUTSIDE_BASE sentence.

    The workspace row stays: `PRAGMA foreign_keys=ON` rejects a missing
    `workspace_id` on the worktree, and `_refused` could not write
    `unresolved_reason` on a dangling FK. Chat already stubs this seam the
    same way. `create=True` so the test collects before checkout_release
    imports `_service_for`.
    """
    from loregarden.services import checkout_release as checkout_release_mod
    from loregarden.services.checkout_release import retire_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)

    with mock.patch.object(checkout_release_mod, "_service_for", return_value=None, create=True):
        report = retire_checkout(session, row, str(tree))

    assert report.outcome is RemovalOutcome.REFUSED
    assert report.reason == "workspace_gone"
    assert report.reason not in {"unreadable", "cleanup_failed"}
    assert "Nothing was deleted" in report.detail
    assert _WORKSPACE_GONE_CLAUSE in report.detail
    assert _OUTSIDE_BASE_SENTENCE not in report.detail
    assert tree.is_dir()
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE.value
    assert row.unresolved_reason
    assert _WORKSPACE_GONE_CLAUSE in row.unresolved_reason
    assert _OUTSIDE_BASE_SENTENCE not in row.unresolved_reason


# ---------------------------------------------------------------------------
# lg-external-harness-1488, test-break. The lookup, adversarially.
#
# AC1/AC3/AC4 pin the three named scenarios. They stay green for three mutants
# the production edit has to refuse:
#   R6  WorktreeService(session, repo_path=workspace.repo_path) — absolute
#       tmp_path fixtures never notice that live loregarden stores `.`.
#   R7  WorktreeService.__init__ walks up `.worktrees` — AC3 then passes with
#       workspace.repo_path planted on the nested dispatch root.
#   AC2 a leftover `repo_root=None` / `*args` shim — the new call sites still
#       type-check and the grep of positional uses goes quiet.
# ---------------------------------------------------------------------------


_WORKSPACE_GONE_VALUE = "its workspace is gone"
_CHAT_GONE_CLAUSE = "its workspace could not be resolved, so nothing could be attempted"
_UNREADABLE_CLAUSE = "the filesystem would not say whether it is there"


def _nested_receipted(
    session, tmp_path, *, repo_path: str, slug: str
) -> tuple[Path, Path, Worktree]:
    """A receipted checkout at ``tmp_path/.worktrees/<slug>`` owned by `repo_path`.

    Geometry is production's: the tree lives under the parent of the main
    checkout, not beside it. `repo_path` is stored on the workspace as given —
    `.` for the R6 mutant, `str(main)` otherwise — and the git landing is
    always against the real main checkout.
    """
    main = make_repo(tmp_path, "loregarden")
    workspace = Workspace(slug=slug, name=slug, repo_path=repo_path)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    tree = tmp_path / ".worktrees" / slug
    ticket = Ticket(
        external_id=slug,
        workspace_id=workspace.id,
        title=f"Owner of {slug}",
        branch=f"loregarden/{slug}",
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code=f"run-{slug}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(main, "worktree", "add", "-q", "-b", ticket.branch, str(tree))
    (tree / "landed.txt").write_text("the work this checkout held\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "work")
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=str(tree),
        branch=ticket.branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    grant_receipt(session, ticket, main, branch=ticket.branch)
    assert tree.is_relative_to(main.parent / ".worktrees")
    assert workspace.repo_path == repo_path
    return main, tree, row


def test_a_relative_workspace_repo_path_still_scopes_the_gate_from_the_main_checkout(
    session, tmp_path
):
    """R6. Live loregarden stores `Workspace.repo_path` as `.`.

    # CHECKPOINT: relative `.` is resolved through `resolve_workspace_root`
    against `settings.repo_root`, not through `Path(workspace.repo_path)`.
    Inlining `WorktreeService(session, repo_path=workspace.repo_path)` keeps
    every absolute tmp_path fixture green and refuses this tree as
    OUTSIDE_BASE, which is the production lie.
    """
    from loregarden.services.checkout_release import release_checkout
    from loregarden.services.worktree_lifecycle import _service_for as real_service_for

    main, tree, row = _nested_receipted(session, tmp_path, repo_path=".", slug="lg-1488-relative")

    with (
        mock.patch(
            "loregarden.services.workspace_paths.settings.repo_root",
            main.resolve(),
        ),
        mock.patch(
            "loregarden.services.checkout_release._service_for",
            wraps=real_service_for,
            create=True,
        ) as spy,
    ):
        report = release_checkout(session, str(tree))

    spy.assert_called_once_with(session, row.workspace_id)
    assert report.outcome is RemovalOutcome.DELETED, (
        f"a relative repo_path of '.' must still name {main.parent / '.worktrees'} "
        f"as the base; got {report.outcome} / {report.reason!r}: {report.detail}"
    )
    assert report.reason == ""
    assert _OUTSIDE_BASE_SENTENCE not in report.detail
    assert not tree.exists()
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value
    session.refresh(session.get(Workspace, row.workspace_id))
    assert session.get(Workspace, row.workspace_id).repo_path == "."


def test_retire_checkout_asks_service_for_the_rows_workspace(session, workspace, repo):
    """AC1's placement, on the success path. AC1 only stubs `_service_for` to
    None. Copying `session.get` + `resolve_workspace_root` without calling the
    helper still deletes an absolute-path fixture and skips the None branch.
    """
    from loregarden.services import checkout_release as checkout_release_mod
    from loregarden.services.checkout_release import retire_checkout
    from loregarden.services.worktree_lifecycle import _service_for as real_service_for

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)

    with mock.patch.object(
        checkout_release_mod, "_service_for", wraps=real_service_for, create=True
    ) as spy:
        report = retire_checkout(session, row, str(tree))

    spy.assert_called_once_with(session, row.workspace_id)
    assert report.outcome is RemovalOutcome.DELETED
    assert not tree.exists()


def test_a_gone_workspace_refusal_is_a_premise_not_a_park_or_a_crash(session, workspace, repo):
    """AC8/AC9. WORKSPACE_GONE is `_PREMISE_VERDICT` material: REFUSED, empty
    parked_path, the enum value in the sentence the reader gets. A PARKED
    outcome or a missing value leaves the operator grouping on a key whose
    sentence never named the premise.
    """
    from loregarden.services import checkout_release as checkout_release_mod
    from loregarden.services.checkout_release import retire_checkout

    row = _receipted(session, workspace, repo)
    tree = Path(row.worktree_path)

    with mock.patch.object(checkout_release_mod, "_service_for", return_value=None, create=True):
        first = retire_checkout(session, row, str(tree))
        second = retire_checkout(session, row, str(tree))

    assert first.outcome is RemovalOutcome.REFUSED
    assert first.outcome is not RemovalOutcome.PARKED
    assert first.parked_path == ""
    assert first.reason == "workspace_gone"
    assert _WORKSPACE_GONE_VALUE in first.detail
    assert _WORKSPACE_GONE_CLAUSE in first.detail
    assert _CHAT_GONE_CLAUSE not in first.detail
    assert _UNREADABLE_CLAUSE not in first.detail
    assert (first.outcome, first.reason, first.detail, first.parked_path) == (
        second.outcome,
        second.reason,
        second.detail,
        second.parked_path,
    )
    assert tree.is_dir()


def test_workspace_gone_is_a_construction_failure_not_a_tree_shape_premise():
    """AC8 + R3. Value, order, unique clause, and the two places it must not
    appear. Parametrized `list(KeepReason)` tests inject via mock and auto-grow;
    this pin is what stops a copied neighbour clause and a `_keep_reason` case.
    """
    import inspect

    from loregarden.services import worktree_lifecycle
    from loregarden.services.worktree_lifecycle import (
        _REASON_CLAUSE,
        _keep_reason,
        _refused,
        _service_for,
    )
    from loregarden.services.worktree_premises import KeepReason, RemovalFailure

    names = [member.name for member in KeepReason]
    assert names[names.index("UNREADABLE") + 1] == "WORKSPACE_GONE"
    assert names[names.index("WORKSPACE_GONE") + 1] == "CLAIMANT_LIVE"
    assert KeepReason.WORKSPACE_GONE.value == _WORKSPACE_GONE_VALUE
    assert KeepReason.WORKSPACE_GONE.name.lower() == "workspace_gone"
    clause = _REASON_CLAUSE[KeepReason.WORKSPACE_GONE]
    assert clause == _WORKSPACE_GONE_CLAUSE
    neighbours = {
        text for key, text in _REASON_CLAUSE.items() if key is not KeepReason.WORKSPACE_GONE
    }
    assert clause not in neighbours
    assert clause != _CHAT_GONE_CLAUSE
    assert clause != _UNREADABLE_CLAUSE
    assert "WORKSPACE_GONE" not in [member.name for member in RemovalFailure]
    assert "WORKSPACE_GONE" not in inspect.getsource(_keep_reason)
    assert "_service_for" not in worktree_lifecycle.__all__
    assert "_refused" not in worktree_lifecycle.__all__
    assert _service_for.__name__.startswith("_")
    assert _refused.__name__.startswith("_")


def test_a_nested_repo_path_does_not_walk_up_to_the_workspace_worktrees_folder(session, tmp_path):
    """R7. `WorktreeService.__init__` derives `parent / ".worktrees"` and stops.

    Walking up when `repo_path` already lives under `.worktrees` makes AC3
    pass with `workspace.repo_path` set to the nested dispatch root — the
    fixture then no longer represents production, and the lie is untestable.
    """
    from loregarden.services.worktree_service import WorktreeService

    nested = tmp_path / ".worktrees" / "ticket-mine"
    nested.mkdir(parents=True)
    service = WorktreeService(session, repo_path=str(nested))
    assert service.worktree_base == nested.resolve().parent / ".worktrees"
    assert service.worktree_base != nested.resolve().parent


def test_the_gate_no_longer_takes_the_callers_path():
    """AC2. No shim, no default, no `*args`. `ensure_ticket_branch` keeps
    `repo_root` for git cwd; the four names that only forwarded it do not.
    """
    import inspect

    from loregarden.services.branch_triage_service import _release_checkouts_on
    from loregarden.services.checkout_release import release_checkout, retire_checkout
    from loregarden.services.git_branch import _try_free_worktree_lock, ensure_ticket_branch

    dropped = {
        "release_checkout": (release_checkout, ["session", "path"]),
        "retire_checkout": (retire_checkout, ["session", "row", "path"]),
        "_try_free_worktree_lock": (
            _try_free_worktree_lock,
            ["branch", "locked_at", "holder", "session"],
        ),
        "_release_checkouts_on": (_release_checkouts_on, ["session", "worktree_paths"]),
    }
    for name, (func, expected) in dropped.items():
        params = inspect.signature(func).parameters
        assert list(params) == expected, name
        assert "repo_root" not in params, name
        kinds = {item.kind for item in params.values()}
        assert inspect.Parameter.VAR_POSITIONAL not in kinds, name
        assert inspect.Parameter.VAR_KEYWORD not in kinds, name
        for item in params.values():
            assert item.default is inspect.Parameter.empty, f"{name} defaults {item.name}"

    assert "repo_root" in inspect.signature(ensure_ticket_branch).parameters
    lock = inspect.signature(_try_free_worktree_lock).parameters
    assert lock["session"].kind is inspect.Parameter.KEYWORD_ONLY
    assert lock["holder"].kind is inspect.Parameter.KEYWORD_ONLY
