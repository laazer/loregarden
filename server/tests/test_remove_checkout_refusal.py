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

Everything here exercises the REAL service (lg-external-harness-1165 AC5): no
`mock.patch.object(WorktreeService, ...)` at the seam, and no stubbed bool. The
two conditions are forced from outside — a row whose recorded path is outside
the base, and a patched `worktree_service.shutil.rmtree` — because a stub
returning a member would prove only that the test can construct one.
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
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import worktree_service as worktree_service_module
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo

SERVICE_LOGGER = "loregarden.services.worktree_service"


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


def _ticket(session, workspace, external_id="LG-1") -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id.lower()}",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


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

    Asserted as `is None` rather than falsiness: `CleanupRefusal | None` is
    falsy on a member too, which is exactly the inversion the rename exists to
    stop a caller from writing by accident.
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
        refusal = service.remove_checkout(row)

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
        worktree_service_module.shutil, "rmtree", side_effect=OSError(16, "Device or resource busy")
    ) as rmtree:
        refusal = service.remove_checkout(row)

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
        worktree_service_module.shutil, "rmtree", side_effect=PermissionError("nope")
    ):
        raised = service.remove_checkout(raised_row)
    outside_refusal = service.remove_checkout(outside_row)

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
    ]
    assert not {member.value for member in CleanupRefusal} & {member.value for member in KeepReason}


def test_the_service_has_no_attribute_named_cleanup_worktree():
    """AC14 — the rename is load-bearing, so no alias may survive.

    `CleanupRefusal | None` is falsy on success and all five production readers
    were truthiness readers, so a caller left on the old name would silently
    invert: a refusal would read as a removal. The rename turns every one of
    them into an `AttributeError`, and every unmigrated
    `mock.patch.object(WorktreeService, "cleanup_worktree", ...)` into a "does
    not have the attribute" error. A compatibility alias would hand that back.
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

    With `import shutil` inside the removal body there is no module attribute
    to patch, so the only way to force the raising path was a `chmod` recipe
    that is flaky under root and on some filesystems. Asserted on the module
    attribute and on the source, because a function-local import re-added later
    would leave the module attribute in place and quietly stop being the thing
    the removal calls.
    """
    assert worktree_service_module.shutil.__name__ == "shutil"
    source = Path(worktree_service_module.__file__).read_text()
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
        assert service.remove_checkout(row) is CleanupRefusal.OUTSIDE_BASE

    records = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == SERVICE_LOGGER
    ]
    assert records == [], [record.getMessage() for record in records]


def test_the_raising_removal_keeps_its_traceback(session, workspace, service, caplog):
    """AC18 — the exception detail is the one thing the member cannot carry.

    OUTSIDE_BASE loses its record because the return value says everything
    there is to say. REMOVE_RAISED does not: which exception, from which call,
    with which errno, lives only in the traceback. That record is the detail,
    not a duplicate of the caller's sentence, and it is on a different logger
    from the caller's (AC20).
    """
    ticket = _ticket(session, workspace)
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    stray = service.worktree_base / "stray-tree"
    stray.mkdir()
    row = _row_at(session, workspace, ticket, stray)

    with (
        mock.patch.object(
            worktree_service_module.shutil, "rmtree", side_effect=OSError(16, "busy")
        ),
        caplog.at_level(logging.ERROR, logger=SERVICE_LOGGER),
    ):
        assert service.remove_checkout(row) is CleanupRefusal.REMOVE_RAISED

    records = [record for record in caplog.records if record.name == SERVICE_LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None, "the traceback is the whole point of keeping this"


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
    """A workspace and an ACTIVE row for the route to act on.

    `outside=True` records a path the service refuses outright; otherwise the
    row points at a directory under the base that is not a registered
    worktree, so the `shutil.rmtree` fallback is reached and can be made to
    raise. Slug and directory are per-call: two of these live in one test, and
    a shared workspace slug is a unique-constraint failure dressed up as a
    route bug.
    """
    root.mkdir(parents=True, exist_ok=True)
    repo = make_repo(root, name=slug)
    workspace = Workspace(slug=slug, name=slug, repo_path=str(repo))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    ticket = _ticket(db_session, workspace, external_id=f"LG-{slug.upper()}")
    base = repo.parent / ".worktrees"
    base.mkdir(parents=True, exist_ok=True)
    path = (root / "outside-the-base") if outside else (base / f"{slug}-tree")
    path.mkdir(parents=True, exist_ok=True)
    return _row_at(db_session, workspace, ticket, path), path


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
        worktree_service_module.shutil, "rmtree", side_effect=OSError(16, "busy")
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

    refusal = service.remove_checkout(row)

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

    assert service.remove_checkout(row) is CleanupRefusal.OUTSIDE_BASE
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

    refusal = service.remove_checkout(row)

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

    refusal = service.remove_checkout(row)

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
    that it still reports the traceback when nothing in the test arranged the
    exception. A `side_effect` proves the branch exists; this proves the branch
    is reachable from the outside.
    """
    service.worktree_base.mkdir(parents=True, exist_ok=True)
    not_a_tree = service.worktree_base / "a-file-where-a-tree-should-be"
    not_a_tree.write_text("recorded as a checkout, actually a file\n")
    row = _row_at(
        session, workspace, _ticket(session, workspace, external_id="LG-FILE"), not_a_tree
    )

    with caplog.at_level(logging.ERROR, logger=SERVICE_LOGGER):
        refusal = service.remove_checkout(row)

    assert refusal is CleanupRefusal.REMOVE_RAISED
    assert not_a_tree.is_file(), "nothing was removed, so the row still owns the path"
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE
    errors = [r for r in caplog.records if r.name == SERVICE_LOGGER and r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None


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

    answers = [service.remove_checkout(row) for _ in range(attempt)]

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
