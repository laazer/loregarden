"""The removal seam answers *which* refusal, not just "no".

`WorktreeService.cleanup_worktree` returned `False` for two conditions an
operator has to respond to differently: a recorded path outside the service's
base, which nothing will ever make removable, and an exception out of `git
worktree remove` plus the `shutil.rmtree` fallback, which the next attempt
often clears. Collapsed into one bool, the only message anyone could write was
the retryable one — so lg-external-harness-954's toast told an operator to
clear a path in the case where clearing the path is not the remedy, and
`reconcile_worktrees` logged the same transient-sounding warning on every boot,
forever, for a row no boot can settle.

lg-external-harness-1026 kept this seam's NAME and changed its contract: a
value a caller can forget to read is the same defect one shape along from the
bool, so the seam RAISES `DirectoryReleaseFailed` and `retire_worktree` is the
one place that catches it. The member 1165 bought travels ON the exception,
beside the parked path 1026 needs, so every assertion here reads the same
vocabulary it always did — through `_refusal_of` rather than off a return
value. `None` is still the whole success case, because a command that returns
normally said everything there is to say.

Everything here exercises the REAL service (lg-external-harness-1165 AC5): no
`mock.patch.object(WorktreeService, ...)` at the seam, and no stubbed answer.
The two conditions are forced from outside — a row whose recorded path is
outside the base, and a patched `directory_release.shutil.rmtree` (the delete
moved there with `release_directory`, 1026 AC9/AC14) — because a stub raising
a member would prove only that the test can construct one.
"""

from __future__ import annotations

import logging
import subprocess
from enum import Enum, StrEnum
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import directory_release as directory_release_module
from loregarden.services import worktree_service as worktree_service_module
from loregarden.services.directory_release import DirectoryReleaseFailed
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import (
    git,
    grant_receipt,
    make_repo,
)
from tests.worktree_helpers import (
    make_ticket_on_id_branch as _ticket,
)

SERVICE_LOGGER = "loregarden.services.worktree_service"
RELEASE_LOGGER = "loregarden.services.directory_release"


def _refusal_of(service: WorktreeService, row: Worktree) -> CleanupRefusal:
    """The member the seam raised with, for a row it refuses.

    One helper rather than a `pytest.raises` block at every site, because the
    thing each test below is about is WHICH member — and a raise spelled out
    seventeen times is seventeen places to get the exception type wrong.

    Asserts the raise happened: a seam that answered the member instead of
    raising it would otherwise make every caller here fail on a confusing
    `AttributeError` three lines later rather than on the contract.
    """
    with pytest.raises(DirectoryReleaseFailed) as caught:
        service.remove_checkout(row)
    return caught.value.refusal


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


@pytest.fixture(name="service")
def service_fixture(session, repo):
    return WorktreeService(session, repo_path=str(repo))


def _run(session, workspace, ticket, code="r1") -> AgentRun:
    run = AgentRun(
        run_code=f"{code}-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _real_tree(session, workspace, ticket) -> tuple[Path, Worktree]:
    """A registered worktree under the service's base, with a commit on it."""
    root = resolve_execution_root(session, _run(session, workspace, ticket), ticket, workspace)
    (root / "work.txt").write_text("real work\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "work")
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    return root, row


def _row_at(session, workspace, ticket, path: Path) -> Worktree:
    """An ACTIVE row recording `path`, with nothing cut there by git."""
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=_run(session, workspace, ticket, code="r-row").id,
        ticket_id=ticket.id,
        worktree_path=str(path),
        branch=ticket.branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --------------------------------------------------------------------------
# AC1 — the three answers, from the real service
# --------------------------------------------------------------------------


def test_a_checkout_that_goes_answers_none(session, workspace, service):
    """AC1 — `None` is "the row is settled", and it is the whole success case.

    Asserted as `is None` rather than as "it did not raise": the seam is a
    command, so a caller that read its answer for anything would be reading a
    value with no meaning, and the day it grows one the `None` is the pin that
    notices.
    """
    ticket = _ticket(session, workspace)
    root, row = _real_tree(session, workspace, ticket)

    assert service.remove_checkout(row) is None

    assert not root.exists()
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP
    assert row.cleaned_at is not None


def test_a_row_whose_directory_is_already_gone_is_settled(session, workspace, service):
    """AC1 — "nothing was on disk to unlink" is settled, not a refusal.

    This is today's `True` for a path the last process already removed, and
    `reconcile_worktrees` depends on it: its first branch exists precisely for
    rows whose directory is gone, and answering a refusal here would make every
    boot report a permanent problem about stale bookkeeping.
    """
    ticket = _ticket(session, workspace)
    root, row = _real_tree(session, workspace, ticket)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(root)],
        cwd=session.get(Workspace, workspace.id).repo_path,
        check=True,
        capture_output=True,
    )
    assert not root.exists()

    assert service.remove_checkout(row) is None

    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP


def test_a_recorded_path_outside_the_base_is_refused_without_attempting_anything(
    session, workspace, service, tmp_path
):
    """AC1/AC5 — OUTSIDE_BASE, and nothing was tried.

    Forced by the row, not by a stub: the path is outside
    `service.worktree_base`, which is a configuration or programming fault no
    retry can clear. "Nothing was attempted" is the half that makes the member
    mean something different from REMOVE_RAISED, so it is asserted directly —
    the directory survives untouched, no git command is issued, and the row is
    left ACTIVE rather than stamped CLEANUP.
    """
    ticket = _ticket(session, workspace)
    outside = tmp_path / "somewhere-else" / "tree"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("not ours to delete\n")
    row = _row_at(session, workspace, ticket, outside)

    calls: list[list[str]] = []

    def spying_run_git(args, **kwargs):
        calls.append(list(args))
        return worktree_service_module.run_git(args, **kwargs)

    with mock.patch.object(worktree_service_module, "run_git", spying_run_git):
        refusal = _refusal_of(service, row)

    assert refusal is CleanupRefusal.OUTSIDE_BASE
    assert calls == [], f"the guard must refuse before touching git; got {calls}"
    assert (outside / "keep.txt").read_text() == "not ours to delete\n"
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE
    assert row.cleaned_at is None


def test_a_removal_that_raises_answers_remove_raised(session, workspace, service, tmp_path):
    """AC1/AC5/AC19 — REMOVE_RAISED, forced through the real removal.

    The directory is under the base but is not a registered worktree, so `git
    worktree remove` fails and the `shutil.rmtree` fallback is really reached —
    which is what lets a patched `worktree_service.shutil.rmtree` stand in for
    the permission error, busy file or NFS stall this member is about. The
    patch point only exists because AC19 hoists `import shutil` to module
    level; a `chmod`-based recipe is rejected as flaky under root and on some
    filesystems.
    """
    ticket = _ticket(session, workspace)
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    stray = service.worktree_base / "stray-tree"
    stray.mkdir()
    (stray / "held-open.txt").write_text("a process has this\n")
    row = _row_at(session, workspace, ticket, stray)

    with mock.patch.object(
        directory_release_module.shutil,
        "rmtree",
        side_effect=OSError(16, "Device or resource busy"),
    ) as rmtree:
        refusal = _refusal_of(service, row)

    assert refusal is CleanupRefusal.REMOVE_RAISED
    rmtree.assert_called_once()
    # The removal was attempted and did not happen: the directory is still
    # there and the row still owns it, so a later attempt is a real retry.
    assert stray.is_dir()
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE


def test_the_two_refusals_are_not_the_same_answer(session, workspace, service, tmp_path):
    """AC1 — the point of the ticket, in one assertion.

    Each test above would pass against an implementation that returned one
    member for both conditions. This runs both and asserts they differ, which
    is the only shape that catches the collapse coming back.
    """
    outside_ticket = _ticket(session, workspace, external_id="LG-OUTSIDE")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    outside_row = _row_at(session, workspace, outside_ticket, outside)

    raised_ticket = _ticket(session, workspace, external_id="LG-RAISED")
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    stray = service.worktree_base / "stray-two"
    stray.mkdir()
    raised_row = _row_at(session, workspace, raised_ticket, stray)

    with mock.patch.object(
        directory_release_module.shutil, "rmtree", side_effect=PermissionError("nope")
    ):
        raised = _refusal_of(service, raised_row)
    outside_refusal = _refusal_of(service, outside_row)

    assert raised is not outside_refusal
    assert {raised, outside_refusal} == set(CleanupRefusal)


# --------------------------------------------------------------------------
# AC15 / AC14 / AC19 — the vocabulary's kind, home and reachability
# --------------------------------------------------------------------------


def test_cleanup_refusal_is_a_plain_enum_of_exactly_two_machine_slugs():
    """AC15 — not a `StrEnum`, because the values are not operator copy.

    `KeepReason` is a `StrEnum` precisely because its values ARE the wording a
    caller prints. These are not: the boot log (AC13) and the delete toast
    (AC9) need DIFFERENT sentences for the SAME cause, so one string per member
    cannot be the copy for both. A plain `Enum` makes that structural instead
    of a convention the next author has to remember.
    """
    assert issubclass(CleanupRefusal, Enum)
    assert not issubclass(CleanupRefusal, StrEnum)
    assert not issubclass(CleanupRefusal, str)
    assert {member.name: member.value for member in CleanupRefusal} == {
        "OUTSIDE_BASE": "outside_base",
        "REMOVE_RAISED": "remove_raised",
    }


def test_cleanup_refusal_lives_beside_the_modules_other_failure_vocabulary():
    """AC15 — declared in `worktree_service`, where the failure it names is.

    `worktree_lifecycle` imports `worktree_service`, so declaring it there
    would be an import cycle, and function-local imports to dodge a cycle are
    banned here. It is not persisted, so it does not belong in
    `models/domain/enums.py` either.
    """
    assert CleanupRefusal.__module__ == "loregarden.services.worktree_service"
    assert CleanupRefusal.__doc__, "the docstring carries why this is not a StrEnum"


def test_cleanup_refusal_is_not_merged_into_keep_reason():
    """AC15/AC2 — two vocabularies, because they answer two questions.

    `KeepReason` answers "which premise chose not to remove it";
    `CleanupRefusal` answers "the removal was attempted and did not happen".
    Merging them re-collapses exactly what lg-external-harness-954 split, so
    the three members are pinned here as well as in the lifecycle module.
    """
    from loregarden.services.worktree_lifecycle import KeepReason

    assert [member.name for member in KeepReason] == [
        "UNCOMMITTED",
        "STRANDED_HEAD",
        "NOTHING_COMMITTED",
        "NO_RECEIPT",
        "RECEIPT_DOES_NOT_COVER",
        "CLAIM_UNPROVEN",
        "UNREADABLE",
        "CLAIMANT_LIVE",
    ]
    assert not {member.value for member in CleanupRefusal} & {member.value for member in KeepReason}


def test_the_service_has_no_attribute_named_cleanup_worktree():
    """AC14 — the rename is load-bearing, so no alias may survive.

    All five production readers were truthiness readers of a bool, so a caller
    left on the old name would silently invert: a refusal would read as a
    removal. The rename turns every one of them into an `AttributeError`, and
    every unmigrated `mock.patch.object(WorktreeService, "cleanup_worktree",
    ...)` into a "does not have the attribute" error. A compatibility alias
    would hand that back — and so would an alias re-added for
    lg-external-harness-1026, whose AC14 names the old spelling.
    """
    assert not hasattr(WorktreeService, "cleanup_worktree")
    assert callable(WorktreeService.remove_checkout)


def test_the_route_handler_keeps_its_own_name():
    """AC14 — only the service method is renamed.

    `api/parallel.py::cleanup_worktree` is the route's handler, not the seam.
    Renaming it would change an operation id for no reason, so the fence is
    pinned in both directions rather than left to a careless sweep.
    """
    from loregarden.api import parallel

    assert callable(parallel.cleanup_worktree)


def test_shutil_is_imported_at_module_level(session, workspace, service):
    """AC19 — the repo's import rule, and what makes AC5's recipe possible.

    With `import shutil` inside the delete body there is no module attribute to
    patch, so the only way to force the raising path was a `chmod` recipe that
    is flaky under root and on some filesystems. Asserted on the module
    attribute and on the source, because a function-local import re-added later
    would leave the module attribute in place and quietly stop being the thing
    the delete calls.

    Asserted on `directory_release`, where lg-external-harness-1026 AC9/AC14
    moved the delete, and on `worktree_service` as well: the seam keeps the row
    update, so a `shutil` left behind there is a second delete path beside the
    chokepoint.
    """
    assert directory_release_module.shutil.__name__ == "shutil"
    for module in (directory_release_module, worktree_service_module):
        source = Path(module.__file__).read_text()
        body_imports = [
            line
            for line in source.splitlines()
            if line.strip().startswith(("import shutil", "from shutil")) and line.startswith(" ")
        ]
        assert body_imports == [], f"hoist these to module level: {body_imports}"


# --------------------------------------------------------------------------
# AC18 — one record per row, and the traceback stays where the cause is
# --------------------------------------------------------------------------


def test_the_out_of_base_refusal_is_not_also_logged_by_the_service(
    session, workspace, service, tmp_path, caplog
):
    """AC18 — the typed return IS the surfacing, so the old `logger.error` goes.

    The callers now report this with the row id and the base directory AC13
    requires. Keeping the service's own record beside that is two records for
    one row, which is how one fault starts reading as two unrelated-looking
    lines. Not a silent failure: the member is returned, and every caller is
    obliged to read it.
    """
    ticket = _ticket(session, workspace)
    row = _row_at(session, workspace, ticket, tmp_path / "outside" / "tree")

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        assert _refusal_of(service, row) is CleanupRefusal.OUTSIDE_BASE

    records = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == SERVICE_LOGGER
    ]
    assert records == [], [record.getMessage() for record in records]


def test_the_raising_removal_keeps_its_traceback(session, workspace, service, caplog):
    """AC18 + 1026 AC10 — the detail the member cannot carry, on the raise.

    OUTSIDE_BASE needed no record because the answer said everything there is
    to say. REMOVE_RAISED does: which exception, from which call, with which
    errno. 1165 kept that in a `logger.error(..., exc_info=True)` because a
    returned member had nowhere to put it; 1026's raise does, as `__cause__`,
    and that is strictly better than a log line — a caller can report it, and
    nothing has to find the record that matches the row.

    So the record is GONE here and the cause is asserted instead. The one
    surviving ERROR record in this family belongs to `directory_release` and
    only to the case the exception cannot describe: a park that could not be
    restored, where the bytes are at a path only the log knows (AC10). A
    successful restore, which is this test, logs nothing.
    """
    ticket = _ticket(session, workspace)
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    stray = service.worktree_base / "stray-tree"
    stray.mkdir()
    row = _row_at(session, workspace, ticket, stray)
    busy = OSError(16, "busy")

    with (
        mock.patch.object(directory_release_module.shutil, "rmtree", side_effect=busy),
        caplog.at_level(logging.ERROR),
        pytest.raises(DirectoryReleaseFailed) as caught,
    ):
        service.remove_checkout(row)

    assert caught.value.refusal is CleanupRefusal.REMOVE_RAISED
    assert caught.value.__cause__ is busy, "the original failure travels, not a summary of it"
    records = [
        record
        for record in caplog.records
        if record.name in {SERVICE_LOGGER, RELEASE_LOGGER} and record.levelno >= logging.ERROR
    ]
    assert records == [], [record.getMessage() for record in records]


# --------------------------------------------------------------------------
# AC6 — the vocabulary is server-side, and stays there
# --------------------------------------------------------------------------


def test_no_cleanup_refusal_vocabulary_reaches_the_client():
    """AC6 — exactly one rendered surface changes, and it changes in words only.

    The member is how the server picks a sentence. Nothing ships it: no
    payload, field or event carries `outside_base` or `remove_raised`, so the
    client has no branch to grow on a vocabulary it cannot see. Asserted as a
    search rather than trusted to review, because the cheapest way for this to
    leak is a response model someone widens "for the UI".
    """
    client_src = Path(worktree_service_module.__file__).parents[3] / "client" / "src"
    assert client_src.is_dir(), f"the search is wrong, not the code: {client_src}"

    needles = ["CleanupRefusal", *(member.value for member in CleanupRefusal)]
    offenders = {
        str(path.relative_to(client_src)): [n for n in needles if n in path.read_text()]
        for path in client_src.rglob("*.ts*")
    }
    assert {path: hits for path, hits in offenders.items() if hits} == {}


# --------------------------------------------------------------------------
# AC21 — the manual-cleanup route reads the member too
# --------------------------------------------------------------------------


def _route_row(
    db_session: Session, root: Path, slug: str, *, outside: bool
) -> tuple[Worktree, Path]:
    """A workspace and an ACTIVE row for the route to act on, WITH a receipt.

    `outside=True` records a path the service refuses outright, and records it
    ABSENT: lg-external-harness-1026's `_keep_reason` runs before the seam, and
    a directory under no git repository answers "dirty" (a non-zero `git
    status` fails closed), so an outside path that EXISTS is refused by a
    premise and never reaches the arm this is about. Otherwise the row points
    at a real registered worktree with a commit, which the premises pass and
    whose delete can be made to raise.

    The receipt is granted either way, because 1026 made it the FIRST premise:
    without it every row here refuses on `NO_RECEIPT` and the route reports
    that instead of the cause under test.

    Slug and directory are per-call: two of these live in one test, and a
    shared workspace slug is a unique-constraint failure dressed up as a route
    bug.
    """
    root.mkdir(parents=True, exist_ok=True)
    repo = make_repo(root, name=slug)
    workspace = Workspace(slug=slug, name=slug, repo_path=str(repo))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    ticket = _ticket(db_session, workspace, external_id=f"LG-{slug.upper()}")
    if outside:
        base = repo.parent / ".worktrees"
        base.mkdir(parents=True, exist_ok=True)
        path = root / "outside-the-base"
        git(repo, "branch", ticket.branch)
        grant_receipt(db_session, ticket, repo)
        return _row_at(db_session, workspace, ticket, path), path

    run = _run(db_session, workspace, ticket, code="r-route")
    path = resolve_execution_root(db_session, run, ticket, workspace)
    (path / "work.txt").write_text("real work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")
    grant_receipt(db_session, ticket, repo)
    row = db_session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    return row, path


def test_the_cleanup_route_answers_200_when_the_row_settles(
    client: TestClient, db_session: Session, tmp_path
):
    """AC21 — the existing 200 shape survives, keyed on `None`.

    The route was a truthiness reader (`success = ...cleanup_worktree(...)`),
    so a migration that kept the truthiness test would now answer 200 for both
    refusals. The success path is pinned beside them for that reason.
    """
    row, path = _route_row(db_session, tmp_path / "settles", "settles", outside=False)

    res = client.post(f"/api/parallel/worktree/{row.id}/cleanup")

    assert res.status_code == 200
    assert res.json()["status"] == "cleaned"
    assert not path.exists()


def test_the_cleanup_route_names_which_cause_refused_it(
    client: TestClient, db_session: Session, tmp_path
):
    """AC21 — its existing 400, with a detail that distinguishes the two causes.

    This route has no caller in `client/**`, so changing the detail changes no
    rendered surface and AC6's "exactly one rendered surface changes" holds.
    The assertion is that the two details differ, not what either says: the
    operator copy that IS pinned is AC9's and AC10's, in the chat toast.
    """
    outside_row, _ = _route_row(db_session, tmp_path / "a", "outsidebase", outside=True)
    raised_row, raised_path = _route_row(db_session, tmp_path / "b", "removeraised", outside=False)

    outside_res = client.post(f"/api/parallel/worktree/{outside_row.id}/cleanup")
    with mock.patch.object(
        directory_release_module.shutil, "rmtree", side_effect=OSError(16, "busy")
    ):
        raised_res = client.post(f"/api/parallel/worktree/{raised_row.id}/cleanup")

    assert outside_res.status_code == 400
    assert raised_res.status_code == 400
    outside_detail = outside_res.json()["detail"]
    raised_detail = raised_res.json()["detail"]
    assert outside_detail != raised_detail, (
        "a detail that reads the same for both causes is the collapse this ticket closes, "
        "moved one layer out"
    )
    assert raised_path.is_dir()


# --------------------------------------------------------------------------
# AC1, adversarially — "under the base" is containment, not a string prefix
#
# The guard this seam inherits asked `str(path).startswith(str(base))`, which
# answers a question about text rather than about the filesystem. Two shapes
# satisfy that text and are not under the base at all: a SIBLING directory
# whose name merely extends the base's (`.worktrees-salvage` beside
# `.worktrees`), and a path that climbs back out through `..`. Both are
# destructive the wrong way round — the service removes a tree it does not
# own and answers "settled" — and both are deterministic, so they are pinned
# as a matrix rather than as the one case somebody happened to think of.
#
# `..` is the reason containment has to be decided on a RESOLVED path:
# `Path("<base>/t/../../escaped").is_relative_to(base)` is True, because
# `is_relative_to` is lexical. Resolving first is what makes the answer about
# where the removal would actually land.
# --------------------------------------------------------------------------


def _outside_shapes(base: Path, tmp_path: Path) -> dict[str, Path]:
    """Recorded paths that are not under `base`, by the way they escape it."""
    return {
        # Kills `startswith`: a sibling of the base whose name extends it.
        "sibling_whose_name_extends_the_base": base.parent / f"{base.name}-salvage" / "tree",
        # Kills a lexical `is_relative_to`: inside the base, then back out.
        "climbs_out_through_dotdot": base / "t" / ".." / ".." / "escaped-tree",
        # The plain cases, so the matrix still covers them after a rewrite.
        "an_unrelated_absolute_directory": tmp_path / "unrelated" / "tree",
        "the_bases_own_parent": base.parent,
    }


@pytest.mark.parametrize("shape", sorted(_outside_shapes(Path("/b/.worktrees"), Path("/t"))))
def test_every_way_a_recorded_path_escapes_the_base_is_outside_base(
    session, workspace, service, tmp_path, shape
):
    """AC1 — the member is decided by containment, for every shape of escape.

    Parametrized over the shapes rather than written once per case: the defect
    is a *family* (there are as many ways to spell a path outside a directory
    as the filesystem has syntax), and a family needs a matrix that a later
    author extends, not four tests they have to notice.

    The assertion is in two halves, because either half alone passes against a
    wrong implementation: the member says the operator is told the truth, and
    the surviving directory says the service did not delete something it does
    not own while saying so.
    """
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    target = _outside_shapes(service.worktree_base, tmp_path)[shape]
    target.mkdir(parents=True, exist_ok=True)
    (target / "not-ours.txt").write_text("another process owns this\n")
    row = _row_at(
        session, workspace, _ticket(session, workspace, external_id=f"LG-{shape}"), target
    )

    refusal = _refusal_of(service, row)

    assert refusal is CleanupRefusal.OUTSIDE_BASE
    assert (target / "not-ours.txt").read_text() == "another process owns this\n", (
        f"the {shape} shape was removed: the guard answered a question about text, "
        "not about where the removal lands"
    )
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE
    assert row.cleaned_at is None


def test_a_row_with_no_recorded_path_is_refused_as_permanently_as_one_outside(
    session, workspace, service
):
    """AC1 — an empty `worktree_path` is never retryable either.

    `worktree_path` defaults to `""` on the model, and `reconcile_worktrees`
    hands such a row to the seam on every boot (its first branch is "no path,
    or not a directory"). Empty is not under the base, and no restart will put
    it there, so answering the retryable member would promise a boot that
    clears it — which is the exact confusion this ticket exists to remove, in
    the one case where the row carries no information at all.
    """
    row = _row_at(session, workspace, _ticket(session, workspace, external_id="LG-EMPTY"), Path())
    row.worktree_path = ""
    session.add(row)
    session.commit()

    assert _refusal_of(service, row) is CleanupRefusal.OUTSIDE_BASE
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE


def test_a_symlink_under_the_base_cannot_be_used_to_remove_what_it_points_at(
    session, workspace, service, tmp_path
):
    """AC1 — containment is about the removal's target, not its spelling.

    A link under the base is the one path that *is* lexically inside it and
    whose removal lands outside. Which member answers is deliberately not
    pinned — nothing in the criteria chooses between "refused outright" and
    "the removal was attempted and raised", and both are honest. What is
    pinned is the half that matters: the answer is a refusal, not `None`, and
    the directory on the other end of the link still has its contents.
    """
    outside = tmp_path / "pointed-at"
    outside.mkdir()
    (outside / "payload.txt").write_text("not under any base\n")
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    link = service.worktree_base / "looks-like-ours"
    link.symlink_to(outside, target_is_directory=True)
    row = _row_at(session, workspace, _ticket(session, workspace, external_id="LG-LINK"), link)

    refusal = _refusal_of(service, row)

    assert refusal is not None, "a removal that lands outside the base is not a settled row"
    assert refusal in set(CleanupRefusal)
    assert (outside / "payload.txt").read_text() == "not under any base\n"


def test_a_symlink_outside_the_base_cannot_be_used_to_remove_what_is_inside(
    session, workspace, service, tmp_path
):
    """AC1 — the inverse direction, which is the one that destroys data.

    The test above records a link *under* the base pointing out. This records
    a link *outside* the base pointing in, and unlike that one it can pin the
    member: nothing is attempted, so the answer is exactly OUTSIDE_BASE.

    Resolution follows symlinks, so a resolved-only guard answers "contained"
    here and admits the row. `git worktree remove --force <link>` then follows
    the link and deletes the real checkout at the other end — which belongs to
    a *different*, still-ACTIVE row — after which the dangling link reports
    `exists() is False`, the `shutil` fallback is skipped, and the row is
    stamped CLEANUP with the seam answering `None`. The recorded spelling is
    the only thing that refuses it, which is why containment is checked on
    both the spelling and the resolution.
    """
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    inside = service.worktree_base / "belongs-to-another-row"
    inside.mkdir()
    (inside / "uncommitted.txt").write_text("someone else's unpublished work\n")
    link = tmp_path / "outside-link"
    link.symlink_to(inside, target_is_directory=True)
    row = _row_at(session, workspace, _ticket(session, workspace, external_id="LG-LINK-IN"), link)

    refusal = _refusal_of(service, row)

    assert refusal is CleanupRefusal.OUTSIDE_BASE
    assert (inside / "uncommitted.txt").read_text() == "someone else's unpublished work\n", (
        "the link was followed: a resolved-only guard admits a path spelled "
        "outside the base, and the removal lands on another row's checkout"
    )
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE
    assert row.cleaned_at is None


# --------------------------------------------------------------------------
# AC5 — REMOVE_RAISED with nothing patched at all
#
# The recipe above makes `shutil.rmtree` raise, which is the only way to
# stand in for an NFS stall. But every assertion about REMOVE_RAISED then
# rests on a mock, and a mock cannot tell you that the real `except` is wide
# enough to catch what the real filesystem throws. A regular file recorded
# where a worktree should be does: `git worktree remove` refuses it, the path
# still exists, and the real `shutil.rmtree` raises `NotADirectoryError` from
# inside the real body.
# --------------------------------------------------------------------------


def test_a_real_filesystem_error_out_of_the_real_removal_is_remove_raised(
    session, workspace, service, caplog
):
    """AC5/AC18 — the raising path, exercised with zero mocks.

    This is the same member as the patched-`rmtree` test and a different
    claim: that the handler catches what the filesystem actually raises, and
    that it still carries the cause when nothing in the test arranged the
    exception. A `side_effect` proves the branch exists; this proves the branch
    is reachable from the outside.
    """
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    not_a_tree = service.worktree_base / "a-file-where-a-tree-should-be"
    not_a_tree.write_text("recorded as a checkout, actually a file\n")
    row = _row_at(
        session, workspace, _ticket(session, workspace, external_id="LG-FILE"), not_a_tree
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(DirectoryReleaseFailed) as caught:
            service.remove_checkout(row)

    assert caught.value.refusal is CleanupRefusal.REMOVE_RAISED
    assert not_a_tree.is_file(), "nothing was removed, so the row still owns the path"
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE
    # The real filesystem's own error, carried rather than summarised, and no
    # log record: the raise is the surfacing (1026 AC10).
    assert isinstance(caught.value.__cause__, OSError)
    errors = [
        r
        for r in caplog.records
        if r.name in {SERVICE_LOGGER, RELEASE_LOGGER} and r.levelno >= logging.ERROR
    ]
    assert errors == [], [r.getMessage() for r in errors]


# --------------------------------------------------------------------------
# Determinism — reconcile asks again on every boot
# --------------------------------------------------------------------------


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_the_same_condition_answers_the_same_member_however_often_it_is_asked(
    session, workspace, service, tmp_path, attempt
):
    """AC3/AC13 — the answer is a property of the row, not of the attempt.

    `reconcile_worktrees` re-examines every ACTIVE row on every boot, and
    AC13's whole argument — that a reader seeing the permanent record twice
    knows the repeat is not a transient recurrence — is only true if the
    second pass answers what the first did. An implementation that remembered
    its first attempt, or that mutated the row on a refusal, would make the
    second boot say something else about the same unchanged fault.
    """
    outside = tmp_path / "stable" / "tree"
    outside.mkdir(parents=True)
    row = _row_at(session, workspace, _ticket(session, workspace, external_id="LG-STABLE"), outside)

    answers = [_refusal_of(service, row) for _ in range(attempt)]

    assert answers == [CleanupRefusal.OUTSIDE_BASE] * attempt
    assert outside.is_dir()


def test_a_settled_row_asked_a_second_time_is_still_settled(session, workspace, service):
    """AC1 — `None` survives the repeat, so a boot cannot invent a failure.

    The first call removes the checkout; the second finds nothing on disk. If
    the absence read as a refusal, every row settled by one boot would be
    reported as a permanent fault by the next, which is the log noise AC3 is
    about rather than a fix for it.
    """
    root, row = _real_tree(session, workspace, _ticket(session, workspace, external_id="LG-TWICE"))

    assert service.remove_checkout(row) is None
    assert not root.exists()
    assert service.remove_checkout(row) is None


# --------------------------------------------------------------------------
# RELOCATED from `test_open_defect_repros.py` by lg-external-harness-1026
# (AC18). It was an `xfail(strict=True)` repro of a defect with no ticket:
# the containment guard asked whether the recorded path was UNDER
# `worktree_base`, and `Path.is_relative_to` is reflexive, so a row whose
# `worktree_path` IS the base passed it — and the removal then deleted the
# base, taking every workspace's checkouts that share it, while answering
# "settled". Observed live on 2026-10-16.
#
# `release_directory`'s containment is a STRICT descendant (AC9), so the defect
# is closed and the repro XPASSes. It belongs in the suite that covers this
# surface permanently rather than in the open-defect module, and it is kept
# rather than deleted because it is the only test that puts a NEIGHBOUR's
# uncommitted work in the blast radius.
# --------------------------------------------------------------------------


def test_a_row_recording_the_worktrees_base_does_not_remove_every_workspaces_checkout(
    session, workspace, service, tmp_path
):
    """The base is the container, never a checkout.

    Asserted on the neighbour rather than only on the answer, because the
    damage is what made this a defect — a wrong answer about one row is a log
    line, and removing another ticket's uncommitted work is not recoverable.
    """
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    # Two workspaces whose `repo_path` share a parent share one base — on this
    # machine loregarden's and blobert's both resolve to
    # `/Users/jacobbrandt/workspace/.worktrees` (lg-external-harness-1165 AC12
    # measured it), so this neighbour is not a contrived one.
    neighbour = service.worktree_base / "another-workspaces-checkout"
    neighbour.mkdir()
    (neighbour / "work.txt").write_text("another ticket's uncommitted work\n")
    row = _row_at(
        session,
        workspace,
        _ticket(session, workspace, external_id="LG-BASE"),
        service.worktree_base,
    )

    assert _refusal_of(service, row) is CleanupRefusal.OUTSIDE_BASE

    assert neighbour.is_dir(), (
        "the base was removed as though it were a checkout, taking every other "
        "workspace's worktree with it"
    )
    assert (neighbour / "work.txt").read_text() == "another ticket's uncommitted work\n"
    assert service.worktree_base.is_dir()
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE
