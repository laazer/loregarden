"""retire_worktree must not settle a STILL_PARKED row and wipe parked_at.

lg-external-harness-1606. Sibling of 1593, which already skips both census
sites. This is the remaining ABSENT-settle entry: `_settle`, used only by
`retire_worktree`. Clone of 1593's still_parked fixture shape — not an import.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import worktree_lifecycle
from loregarden.services.checkout_release import RemovalOutcome, _as_removal
from loregarden.services.directory_release import DirectoryState, directory_state
from loregarden.services.worktree_lifecycle import (
    _REASON_CLAUSE,
    KeepReason,
    RemovalFailure,
    _clause_for,
    _keep_reason,
    _refusal_detail,
    _settle,
    retire_worktree,
)
from loregarden.services.worktree_service import CleanupRefusal
from sqlmodel import Session, select
from tests.worktree_helpers import make_repo

_PARKED_CHECKOUT_VALUE = "its checkout is parked"
_PARKED_CHECKOUT_CLAUSE = (
    "the recorded checkout is gone, but parked_at still names a live directory"
)
_LIFECYCLE_PATH = Path(worktree_lifecycle.__file__)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="proj", name="proj", repo_path=str(make_repo(tmp_path)))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _under_the_base(workspace, name: str) -> str:
    """A path where the control plane actually cuts checkouts.

    `WorktreeService.worktree_base` is ``repo.parent / ".worktrees"``. A
    recorded path outside it is refused OUTSIDE_BASE, which would look like
    keep for a reason that has nothing to do with the live-park skip.
    """
    return str(Path(workspace.repo_path).parent / ".worktrees" / name)


def _active_row_with_park(session, workspace, *, path: str, park: str, slug: str) -> Worktree:
    """One ACTIVE row for `retire_worktree`: recorded path, parked_at, under the base.

    Built here rather than imported from the 1593 census module — that file is
    a different surface, and coupling fixtures across tickets is out of scope.
    """
    ticket = Ticket(
        external_id=f"lg-{slug}",
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/lg-{slug}",
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code=f"run-lg-{slug}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=path,
        branch=ticket.branch,
        state=WorktreeState.ACTIVE,
        parked_at=park,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _assert_still_parked_untouched(row: Worktree, park_path: str) -> None:
    """The parked_at/state assertion the skip must satisfy, not setup."""
    assert row.state == WorktreeState.ACTIVE.value
    assert row.parked_at == park_path
    assert directory_state(park_path) is DirectoryState.PRESENT


def _retire(session, workspace, row: Worktree):
    service = worktree_lifecycle._service_for(session, workspace.id)
    if service is None:
        raise RuntimeError("fixture: _service_for returned None")
    return retire_worktree(session, service, row)


@pytest.fixture(name="still_parked")
def still_parked_fixture(session, workspace) -> Worktree:
    """ACTIVE, checkout gone, parked_at a live directory under the worktree base.

    Fixture, not test body: a broken setup must ERROR, not look like a skip.
    """
    park = _under_the_base(workspace, "still-parked-park")
    Path(park).mkdir(parents=True, exist_ok=True)
    row = _active_row_with_park(
        session,
        workspace,
        path=_under_the_base(workspace, "still-parked-gone"),
        park=park,
        slug="still-parked",
    )
    if directory_state(row.worktree_path) is not DirectoryState.ABSENT:
        raise RuntimeError(f"fixture: recorded checkout is not ABSENT: {row.worktree_path}")
    if directory_state(park) is not DirectoryState.PRESENT:
        raise RuntimeError(f"fixture: parked_at is not a live directory: {park}")
    return row


@pytest.fixture(name="still_parked_empty_path")
def still_parked_empty_path_fixture(session, workspace) -> Worktree:
    """STILL_PARKED via the empty-path census, not the ABSENT-directory one.

    `_observed_state` maps empty worktree_path to ABSENT. Do not stat it.
    """
    park = _under_the_base(workspace, "still-parked-empty-park")
    Path(park).mkdir(parents=True, exist_ok=True)
    row = _active_row_with_park(session, workspace, path="", park=park, slug="still-parked-empty")
    if row.worktree_path:
        raise RuntimeError(f"fixture: worktree_path is not empty: {row.worktree_path!r}")
    if directory_state(park) is not DirectoryState.PRESENT:
        raise RuntimeError(f"fixture: parked_at is not a live directory: {park}")
    return row


def _assert_parked_checkout_keep(outcome, row: Worktree, park: str) -> None:
    assert outcome.refusal is KeepReason.PARKED_CHECKOUT
    assert outcome.refusal is not None
    assert outcome.refusal is not RemovalFailure.CLEANUP_FAILED
    assert outcome.refusal is not KeepReason.UNREADABLE
    assert outcome.refusal is not KeepReason.NO_RECEIPT
    assert outcome.parked_path == ""
    assert outcome.deleted_nothing is True
    assert outcome.detail == _refusal_detail(
        KeepReason.PARKED_CHECKOUT,
        row,
        parked_path="",
        clause=_clause_for(KeepReason.PARKED_CHECKOUT),
    )
    _assert_still_parked_untouched(row, park)
    removal = _as_removal(row.worktree_path, outcome)
    assert removal.outcome is RemovalOutcome.REFUSED


def test_retire_worktree_leaves_a_still_parked_row(session, workspace, still_parked):
    """AC1 / AC12: the skip's observable, and the named prevention test."""
    park = still_parked.parked_at
    outcome = _retire(session, workspace, still_parked)
    session.refresh(still_parked)
    _assert_parked_checkout_keep(outcome, still_parked, park)


def test_retire_worktree_leaves_a_still_parked_row_with_empty_worktree_path(
    session, workspace, still_parked_empty_path
):
    """AC1: empty-path STILL_PARKED is the same keep. Clone, do not import."""
    park = still_parked_empty_path.parked_at
    outcome = _retire(session, workspace, still_parked_empty_path)
    session.refresh(still_parked_empty_path)
    _assert_parked_checkout_keep(outcome, still_parked_empty_path, park)


def test_settle_skip_is_absent_conjunct_and_sits_before_remove():
    """AC2: skip after `_observed_state` binds `observed`, before `_remove`.

    Source pin: `parked_at_holds_live_directory` and `DirectoryState.ABSENT`
    both appear in `_settle` before the `_remove` call. Do not skip on the
    predicate alone.
    """
    source = inspect.getsource(_settle)
    assert "parked_at_holds_live_directory" in source
    assert "is DirectoryState.ABSENT" in source
    assert source.count("parked_at_holds_live_directory") == 1
    assert "os.path.exists" not in source
    assert "Path.exists" not in source
    observed_at = source.index("_observed_state")
    predicate_at = source.index("parked_at_holds_live_directory")
    premise_at = source.index("_premise_or_uncertainty")
    remove_at = source.index("_remove(")
    assert "DirectoryState.ABSENT" in source[:remove_at]
    assert "parked_at_holds_live_directory" in source[:remove_at]
    assert observed_at < predicate_at < premise_at
    assert predicate_at < remove_at


def test_settle_live_park_skip_does_not_call_remove_or_record_refusal(
    session, workspace, still_parked
):
    """AC2: on the keep path do not call `_remove` / `_refused` / `_record_refusal` / `_unreadable`."""
    park = still_parked.parked_at
    with (
        mock.patch.object(
            worktree_lifecycle, "_remove", wraps=worktree_lifecycle._remove
        ) as remove,
        mock.patch.object(
            worktree_lifecycle, "_refused", wraps=worktree_lifecycle._refused
        ) as refused,
        mock.patch.object(
            worktree_lifecycle, "_record_refusal", wraps=worktree_lifecycle._record_refusal
        ) as record,
        mock.patch.object(
            worktree_lifecycle, "_unreadable", wraps=worktree_lifecycle._unreadable
        ) as unreadable,
    ):
        outcome = _retire(session, workspace, still_parked)
        session.refresh(still_parked)
        remove.assert_not_called()
        refused.assert_not_called()
        record.assert_not_called()
        unreadable.assert_not_called()
    _assert_parked_checkout_keep(outcome, still_parked, park)


def _functions_calling(tree: ast.AST, name: str) -> set[str]:
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):  # py-org: allow-isinstance
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):  # py-org: allow-isinstance
                continue
            if getattr(child.func, "id", None) == name:
                found.add(node.name)
    return found


def test_remove_is_called_only_from_settle_and_settle_one():
    """AC2 / AC12: `_remove` callers in worktree_lifecycle.py. Do not scan disk_usage."""
    tree = ast.parse(_LIFECYCLE_PATH.read_text(encoding="utf-8"))
    assert _functions_calling(tree, "_remove") == {"_settle", "_settle_one"}


def test_a_present_checkout_with_a_leftover_live_park_is_not_the_still_parked_skip(
    session, workspace
):
    """AC2: do not skip on the predicate alone.

    A PRESENT checkout with a leftover live park must still reach `_keep_reason`
    / `_remove`. 1416 is PRESENT + parked_at ABSENT and is OUT of this ticket.
    """
    from loregarden.services.ticket_worktree import resolve_execution_root

    ticket = Ticket(
        external_id="lg-leftover-park",
        workspace_id=workspace.id,
        title="Add the thing",
        branch="loregarden/lg-leftover-park",
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code="run-lg-leftover-park",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    checkout = resolve_execution_root(session, run, ticket, workspace)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    park = _under_the_base(workspace, "leftover-live-park")
    Path(park).mkdir(parents=True, exist_ok=True)
    row.parked_at = park
    session.add(row)
    session.commit()
    session.refresh(row)
    if directory_state(row.worktree_path) is not DirectoryState.PRESENT:
        raise RuntimeError(f"fixture: recorded checkout is not PRESENT: {row.worktree_path}")
    if directory_state(park) is not DirectoryState.PRESENT:
        raise RuntimeError(f"fixture: leftover parked_at is not a live directory: {park}")

    with mock.patch.object(
        worktree_lifecycle, "_keep_reason", wraps=worktree_lifecycle._keep_reason
    ) as keep_reason:
        outcome = _retire(session, workspace, row)
        session.refresh(row)
        keep_reason.assert_called()

    parked_checkout = getattr(KeepReason, "PARKED_CHECKOUT", None)
    assert outcome.refusal is not parked_checkout
    assert directory_state(checkout) is DirectoryState.PRESENT
    assert directory_state(park) is DirectoryState.PRESENT


def test_empty_parked_at_on_a_gone_checkout_is_still_settled(session, workspace):
    """AC3: 1452's original state — empty parked_at still settles.

    Cite, do not duplicate:
    server/tests/test_worktree_lifecycle.py::test_a_row_whose_directory_is_already_gone_is_settled_without_a_receipt
    This case is the same settle under the worktree base.
    """
    gone = _under_the_base(workspace, "empty-park-gone")
    row = _active_row_with_park(session, workspace, path=gone, park="", slug="empty-park")
    if row.parked_at:
        raise RuntimeError(f"fixture: parked_at is not empty: {row.parked_at!r}")
    if directory_state(row.worktree_path) is not DirectoryState.ABSENT:
        raise RuntimeError(f"fixture: recorded checkout is not ABSENT: {row.worktree_path}")

    outcome = _retire(session, workspace, row)
    session.refresh(row)

    assert outcome.refusal is None
    assert row.state == WorktreeState.CLEANUP.value
    assert row.parked_at == ""


def test_a_file_at_parked_at_is_still_settled(session, workspace):
    """AC4: a file is DirectoryState.ABSENT; exists() would call it present."""
    park = Path(_under_the_base(workspace, "park-file"))
    park.parent.mkdir(parents=True, exist_ok=True)
    park.write_text("not a directory\n", encoding="utf-8")
    row = _active_row_with_park(
        session,
        workspace,
        path=_under_the_base(workspace, "park-file-gone"),
        park=str(park),
        slug="park-file",
    )
    if directory_state(str(park)) is not DirectoryState.ABSENT:
        raise RuntimeError(f"fixture: parked_at file is not ABSENT: {park}")

    outcome = _retire(session, workspace, row)
    session.refresh(row)

    assert outcome.refusal is None
    assert row.state == WorktreeState.CLEANUP.value
    assert row.parked_at == ""
    assert park.is_file()


def test_a_vanished_parked_at_is_still_settled(session, workspace):
    """AC4: non-empty parked_at that is ABSENT is still 1452's settle."""
    park = _under_the_base(workspace, "park-vanished")
    Path(park).parent.mkdir(parents=True, exist_ok=True)
    row = _active_row_with_park(
        session,
        workspace,
        path=_under_the_base(workspace, "park-vanished-gone"),
        park=park,
        slug="park-vanished",
    )
    if directory_state(park) is not DirectoryState.ABSENT:
        raise RuntimeError(f"fixture: vanished parked_at is not ABSENT: {park}")

    outcome = _retire(session, workspace, row)
    session.refresh(row)

    assert outcome.refusal is None
    assert row.state == WorktreeState.CLEANUP.value
    assert row.parked_at == ""
    assert directory_state(park) is DirectoryState.ABSENT


def test_oserror_stating_parked_at_keeps_the_row_on_retire(session, workspace):
    """AC5: OSError while stating parked_at is keep. Do not wrap the predicate."""
    import errno

    park = _under_the_base(workspace, "park-unreadable")
    Path(park).mkdir(parents=True, exist_ok=True)
    row = _active_row_with_park(
        session,
        workspace,
        path=_under_the_base(workspace, "park-unreadable-gone"),
        park=park,
        slug="park-unreadable",
    )

    def raise_on_park(path, *args, **kwargs):
        if Path(path) == Path(park):
            raise OSError(errno.EACCES, "permission denied", str(path))
        return directory_state(path, *args, **kwargs)

    with mock.patch.object(worktree_lifecycle, "directory_state", side_effect=raise_on_park):
        outcome = _retire(session, workspace, row)
        session.refresh(row)

    _assert_parked_checkout_keep(outcome, row, park)


def test_settle_does_not_wrap_the_live_park_predicate_in_try_except():
    """AC5: `_settle` reads the predicate's bool; OSError keep is already inside it."""
    source = inspect.getsource(_settle)
    assert "parked_at_holds_live_directory" in source
    tree = ast.parse(source)
    wrapped = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):  # py-org: allow-isinstance
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):  # py-org: allow-isinstance
                continue
            if getattr(child.func, "id", None) == "parked_at_holds_live_directory":
                wrapped = True
    assert wrapped is False


def test_parked_checkout_is_a_keep_reason_after_claimant_live():
    """AC8: member, value, unique clause. Append; do not reorder WORKSPACE_GONE."""
    names = [member.name for member in KeepReason]
    assert names[names.index("WORKSPACE_GONE") + 1] == "CLAIMANT_LIVE"
    assert "PARKED_CHECKOUT" in names
    assert names[names.index("CLAIMANT_LIVE") + 1] == "PARKED_CHECKOUT"
    assert KeepReason.PARKED_CHECKOUT.value == _PARKED_CHECKOUT_VALUE
    assert KeepReason.PARKED_CHECKOUT.value not in {member.value for member in RemovalFailure}
    assert KeepReason.PARKED_CHECKOUT.value not in {member.value for member in CleanupRefusal}
    clause = _REASON_CLAUSE[KeepReason.PARKED_CHECKOUT]
    assert clause == _PARKED_CHECKOUT_CLAUSE
    neighbours = {
        text for key, text in _REASON_CLAUSE.items() if key is not KeepReason.PARKED_CHECKOUT
    }
    assert clause not in neighbours


def test_parked_checkout_is_not_consulted_by_keep_reason():
    """AC8: no `_keep_reason` / `_premise_or_uncertainty` case. Those run on PRESENT."""
    assert "PARKED_CHECKOUT" not in inspect.getsource(_keep_reason)
    assert "PARKED_CHECKOUT" not in inspect.getsource(worktree_lifecycle._premise_or_uncertainty)


def test_empty_path_keep_calls_the_live_park_predicate(session, workspace, still_parked_empty_path):
    """Mutant: skip on empty `worktree_path` without asking the park predicate.

    Empty path is already ABSENT via `_observed_state`. A branch that returns
    keep because the path is empty never states parked_at, so a vanished or
    empty park would also keep. The skip must still call the predicate.
    """
    park = still_parked_empty_path.parked_at
    with mock.patch.object(
        worktree_lifecycle,
        "parked_at_holds_live_directory",
        wraps=worktree_lifecycle.parked_at_holds_live_directory,
    ) as predicate:
        outcome = _retire(session, workspace, still_parked_empty_path)
        session.refresh(still_parked_empty_path)
        predicate.assert_called()
    _assert_parked_checkout_keep(outcome, still_parked_empty_path, park)


def test_empty_worktree_path_and_empty_parked_at_is_still_settled(session, workspace):
    """Mutant: skip on empty `worktree_path` alone. That is 1452, not STILL_PARKED."""
    row = _active_row_with_park(session, workspace, path="", park="", slug="empty-path-empty-park")
    if row.worktree_path:
        raise RuntimeError(f"fixture: worktree_path is not empty: {row.worktree_path!r}")
    if row.parked_at:
        raise RuntimeError(f"fixture: parked_at is not empty: {row.parked_at!r}")

    outcome = _retire(session, workspace, row)
    session.refresh(row)

    assert outcome.refusal is None
    assert row.state == WorktreeState.CLEANUP.value
    assert row.parked_at == ""


def test_empty_worktree_path_and_a_file_at_parked_at_is_still_settled(session, workspace):
    """Mutant: skip on empty path + nonempty parked_at without directory_state."""
    park = Path(_under_the_base(workspace, "empty-path-park-file"))
    park.parent.mkdir(parents=True, exist_ok=True)
    park.write_text("not a directory\n", encoding="utf-8")
    row = _active_row_with_park(
        session, workspace, path="", park=str(park), slug="empty-path-park-file"
    )
    if row.worktree_path:
        raise RuntimeError(f"fixture: worktree_path is not empty: {row.worktree_path!r}")
    if directory_state(str(park)) is not DirectoryState.ABSENT:
        raise RuntimeError(f"fixture: parked_at file is not ABSENT: {park}")

    outcome = _retire(session, workspace, row)
    session.refresh(row)

    assert outcome.refusal is None
    assert row.state == WorktreeState.CLEANUP.value
    assert row.parked_at == ""
    assert park.is_file()


def test_empty_worktree_path_and_vanished_parked_at_is_still_settled(session, workspace):
    """Mutant: skip on empty path + nonempty parked_at without stating it."""
    park = _under_the_base(workspace, "empty-path-park-vanished")
    Path(park).parent.mkdir(parents=True, exist_ok=True)
    row = _active_row_with_park(
        session, workspace, path="", park=park, slug="empty-path-park-vanished"
    )
    if row.worktree_path:
        raise RuntimeError(f"fixture: worktree_path is not empty: {row.worktree_path!r}")
    if directory_state(park) is not DirectoryState.ABSENT:
        raise RuntimeError(f"fixture: vanished parked_at is not ABSENT: {park}")

    outcome = _retire(session, workspace, row)
    session.refresh(row)

    assert outcome.refusal is None
    assert row.state == WorktreeState.CLEANUP.value
    assert row.parked_at == ""
    assert directory_state(park) is DirectoryState.ABSENT


def test_live_park_skip_is_not_inside_remove_or_record_refusal():
    """AC2: skip lives in `_settle`. Do not edit the callees or the predicate body."""
    from loregarden.services.worktree_service import WorktreeService

    forbidden = (
        worktree_lifecycle._remove,
        worktree_lifecycle._record_refusal,
        worktree_lifecycle._settle_one,
        worktree_lifecycle.settle_row_whose_checkout_is_gone,
        WorktreeService.settle_absent_checkout,
    )
    for fn in forbidden:
        source = inspect.getsource(fn)
        assert "PARKED_CHECKOUT" not in source
        assert "parked_at_holds_live_directory" not in source
    predicate_body = inspect.getsource(worktree_lifecycle.parked_at_holds_live_directory)
    assert "PARKED_CHECKOUT" not in predicate_body
    assert "Retirement" not in predicate_body


def test_worktree_state_has_no_still_parked_member():
    """AC2: STILL_PARKED is a row shape, not a WorktreeState."""
    assert "STILL_PARKED" not in [member.name for member in WorktreeState]


def test_settle_does_not_add_a_keep_still_parked_helper():
    """The four-field Retirement inline is the whole skip. No new helper."""
    source = inspect.getsource(worktree_lifecycle)
    for name in ("_keep_still_parked", "_skip_live_park"):
        assert not hasattr(worktree_lifecycle, name)
        assert f"def {name}" not in source
