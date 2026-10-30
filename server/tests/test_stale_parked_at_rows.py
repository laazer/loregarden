"""A worktrees row whose parked_at names a path that is gone, while the checkout is there.

lg-external-harness-1416, AC14.2–AC14.4 / AC14.6 — the re-entry half. The restore
put the tree back at worktree_path and left parked_at pointing at a park copy
that is no longer on disk. Closing the writer is the prevention census; this is
the invariant that catches the state anyway.

Mirror of `test_worktree_rows_have_their_checkout.py`, with the opposite skip
rules: a CLEANUP row with a stale parked_at IS a violation, an empty
worktree_path is not, and a live park copy is left alone.
"""

from __future__ import annotations

import ast
import logging
import os
import stat
from datetime import date
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    InvariantReentry,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.invariants import Violation
from sqlmodel import Session
from tests.worktree_helpers import make_repo

# AC14.4 — the repair sentence, verbatim. Dash is U+2014.
_AC14_SENTENCE = (
    "The directory is at {worktree_path} and is still owned; its parked copy is gone "
    "\u2014 delete it again, or remove the directory by hand."
)
_AC7_RESTORED = (
    "The directory is still at {path} and is still owned, but part of it may be gone "
    "and git no longer lists it as a worktree \u2014 delete it again, or remove the "
    "directory by hand."
)
_AC13_INTACT = "Nothing was deleted: {path} is still owned \u2014 {clause}."

_FORBIDDEN_LIFECYCLE_NAMES = frozenset(
    {
        "settle_row_whose_checkout_is_gone",
        "_mark_cleaned",
        "_record_refusal",
        "_refusal_detail",
        "_REFUSAL_SENTENCE",
    }
)
_PREVENTED_BY = (
    "server/tests/test_worktree_lifecycle.py"
    "::test_every_writer_of_parked_at_sets_it_only_from_an_unrestored_release"
)
_MODULE = (
    Path(__file__).resolve().parents[1] / "loregarden" / "services" / "stale_parked_at_rows.py"
)


@pytest.fixture(name="INVARIANT")
def invariant_fixture():
    """Resolved here rather than at import, so a module that has not registered
    it yet fails these tests and nothing else in the suite's collection."""
    from loregarden.services import invariant_registry

    return invariant_registry.WORKTREE_PARKED_AT_NAMES_A_LIVE_PATH


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


def _row(
    session,
    workspace,
    *,
    path: str,
    parked_at: str,
    external_id: str,
    state: WorktreeState = WorktreeState.ACTIVE,
) -> Worktree:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id}",
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
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=path,
        parked_at=parked_at,
        unresolved_reason="stale parked locator",
        branch=ticket.branch,
        state=state,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _checkout(tmp_path: Path, name: str) -> str:
    path = tmp_path / "checkouts" / name
    path.mkdir(parents=True)
    return str(path)


def _vanished(tmp_path: Path, name: str) -> str:
    return str(tmp_path / "parks" / name)


@pytest.fixture(name="stale")
def stale_fixture(session, workspace, tmp_path) -> Worktree:
    """The state: checkout PRESENT, parked_at non-empty and ABSENT."""
    return _row(
        session,
        workspace,
        path=_checkout(tmp_path, "stale"),
        parked_at=_vanished(tmp_path, "stale.parked"),
        external_id="lg-stale",
    )


@pytest.fixture(name="live_park")
def live_park_fixture(session, workspace, tmp_path) -> Worktree:
    """A live park copy — the locator STILL_PARKED needs. Must be left alone."""
    parked = Path(_vanished(tmp_path, "live.parked"))
    parked.mkdir(parents=True)
    return _row(
        session,
        workspace,
        path=_checkout(tmp_path, "live"),
        parked_at=str(parked),
        external_id="lg-live-park",
    )


def test_the_detector_lists_a_row_whose_parked_copy_is_gone(INVARIANT, session, stale, live_park):
    violations = list(INVARIANT.detect(session))

    assert [v.subject_id for v in violations] == [stale.id]
    assert violations[0].ticket_id == stale.ticket_id
    assert violations[0].entered_at is None
    assert stale.parked_at in violations[0].detail
    assert stale.worktree_path in violations[0].detail
    assert stale.id in violations[0].detail or stale.ticket_id in violations[0].detail


def test_the_repair_clears_both_fields_and_does_not_settle_the_row(
    INVARIANT, session, stale, live_park
):
    """AC14.3. Two column writes. Not 1452's settle. ACTIVE stays ACTIVE."""
    repaired = INVARIANT.repair(session, list(INVARIANT.detect(session)))

    assert repaired == 1
    session.refresh(stale)
    session.refresh(live_park)
    assert stale.state == WorktreeState.ACTIVE.value
    assert stale.parked_at == ""
    assert stale.unresolved_reason == _AC14_SENTENCE.format(worktree_path=stale.worktree_path)
    assert live_park.parked_at != ""
    assert live_park.state == WorktreeState.ACTIVE.value
    assert Path(live_park.parked_at).is_dir()


def test_one_pass_leaves_nothing_for_the_next(INVARIANT, session, stale):
    INVARIANT.repair(session, list(INVARIANT.detect(session)))

    assert list(INVARIANT.detect(session)) == []


def test_a_cleanup_row_with_a_vanished_parked_at_is_a_violation(
    INVARIANT, session, workspace, tmp_path
):
    """AC14.2. No WorktreeState filter. A settled row with a stale parked_at
    is the same false record as an ACTIVE one."""
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "settled"),
        parked_at=_vanished(tmp_path, "settled.parked"),
        external_id="lg-settled-stale",
        state=WorktreeState.CLEANUP,
    )

    assert [v.subject_id for v in INVARIANT.detect(session)] == [row.id]
    assert INVARIANT.repair(session, list(INVARIANT.detect(session))) == 1
    session.refresh(row)
    assert row.state == WorktreeState.CLEANUP.value
    assert row.parked_at == ""
    assert row.unresolved_reason == _AC14_SENTENCE.format(worktree_path=row.worktree_path)


def test_the_invariant_is_registered_scheduled_and_honest_about_its_reentry(INVARIANT):
    """AC14.6."""
    from loregarden.services.invariant_registry import (
        ACTIVE_WORKTREE_ROWS_HAVE_THEIR_CHECKOUT,
        INVARIANTS,
    )
    from loregarden.services.reconciliation import PERIODIC_STEPS

    assert INVARIANT in INVARIANTS
    assert INVARIANT.name == "worktree_parked_at_names_a_live_path"
    assert INVARIANT.ticket == "lg-external-harness-1416"
    assert INVARIANT.statement == (
        "no worktrees row records a parked_at that is gone while its checkout is present"
    )
    assert INVARIANT.reentry is InvariantReentry.EXPECTED
    assert INVARIANT.since == date(2026, 10, 25)
    assert INVARIANT.prevented_by == _PREVENTED_BY
    because = INVARIANT.expected_because
    assert "directory_release._restore" in because
    assert "move it back by hand before the next sweep" in because
    assert "git worktree remove" not in because
    names = [step.name for step in PERIODIC_STEPS]
    checkout = ACTIVE_WORKTREE_ROWS_HAVE_THEIR_CHECKOUT.name
    assert names[names.index(checkout) + 1] == INVARIANT.name


@pytest.fixture(name="two_stale")
def two_stale_fixture(session, workspace, tmp_path) -> list[Worktree]:
    return [
        _row(
            session,
            workspace,
            path=_checkout(tmp_path, "stale-a"),
            parked_at=_vanished(tmp_path, "stale-a.parked"),
            external_id="lg-stale-a",
        ),
        _row(
            session,
            workspace,
            path=_checkout(tmp_path, "stale-b"),
            parked_at=_vanished(tmp_path, "stale-b.parked"),
            external_id="lg-stale-b",
        ),
    ]


def test_the_repair_counts_every_row_it_cleared(INVARIANT, session, two_stale, live_park):
    """n>1, because n=1 cannot tell a count from a constant."""
    repaired = INVARIANT.repair(session, list(INVARIANT.detect(session)))

    assert repaired == 2
    for row in two_stale:
        session.refresh(row)
        assert row.parked_at == ""
        assert row.state == WorktreeState.ACTIVE.value
        assert row.unresolved_reason == _AC14_SENTENCE.format(worktree_path=row.worktree_path)
    session.refresh(live_park)
    assert live_park.parked_at != ""


def test_the_detector_answers_the_same_twice(INVARIANT, session, two_stale, live_park):
    first = sorted(v.subject_id for v in INVARIANT.detect(session))
    second = sorted(v.subject_id for v in INVARIANT.detect(session))

    assert first == second
    assert first == sorted(row.id for row in two_stale)


def test_a_file_at_parked_at_is_a_violation(INVARIANT, session, workspace, tmp_path):
    """AC14.2. Absent is directory_state's answer, not os.path.exists.

    A file where a park directory should be exists() as True and would be
    skipped by an inverted or exists()-shaped detector — and it is ABSENT as
    a directory, so it is the stale locator this invariant exists to clear.
    """
    parked = Path(_vanished(tmp_path, "was-a-park"))
    parked.parent.mkdir(parents=True, exist_ok=True)
    parked.write_text("not a park copy\n", encoding="utf-8")
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "file-park"),
        parked_at=str(parked),
        external_id="lg-file-park",
    )

    assert [v.subject_id for v in INVARIANT.detect(session)] == [row.id]


def test_an_empty_worktree_path_is_not_probed_and_is_not_a_violation(
    INVARIANT, session, workspace, tmp_path
):
    """AC14.2 / R7. Empty is not PRESENT. os.stat('') can read as cwd.

    Skip BEFORE any directory_state call: if the detector stats '', cwd is a
    directory and the row would fire as a false positive.
    """
    row = _row(
        session,
        workspace,
        path="",
        parked_at=_vanished(tmp_path, "pathless.parked"),
        external_id="lg-pathless",
    )

    assert [v.subject_id for v in INVARIANT.detect(session)] == []
    session.refresh(row)
    assert row.parked_at != ""


def test_an_empty_parked_at_is_not_a_violation(INVARIANT, session, workspace, tmp_path):
    """Healthy restored/intact — parked_at is already empty."""
    _row(
        session,
        workspace,
        path=_checkout(tmp_path, "restored"),
        parked_at="",
        external_id="lg-restored",
    )

    assert list(INVARIANT.detect(session)) == []


def test_a_still_parked_row_is_not_stolen_from_1452(INVARIANT, session, workspace, tmp_path):
    """worktree_path ABSENT, parked_at PRESENT: 1452's waiting window.

    The park copy is the only locator of the bytes. Clearing parked_at here
    would destroy it.
    """
    parked = Path(_vanished(tmp_path, "still-parked"))
    parked.mkdir(parents=True)
    row = _row(
        session,
        workspace,
        path=_vanished(tmp_path, "checkout-gone"),
        parked_at=str(parked),
        external_id="lg-still-parked",
    )

    assert list(INVARIANT.detect(session)) == []
    session.refresh(row)
    assert row.parked_at == str(parked)


def test_two_rows_at_one_vanished_parked_at_are_both_cleared(
    INVARIANT, session, workspace, tmp_path
):
    gone = _vanished(tmp_path, "shared.parked")
    rows = [
        _row(
            session,
            workspace,
            path=_checkout(tmp_path, "dup-a"),
            parked_at=gone,
            external_id="lg-dup-a",
        ),
        _row(
            session,
            workspace,
            path=_checkout(tmp_path, "dup-b"),
            parked_at=gone,
            external_id="lg-dup-b",
        ),
    ]

    assert INVARIANT.repair(session, list(INVARIANT.detect(session))) == 2
    for row in rows:
        session.refresh(row)
        assert row.parked_at == ""
        assert row.state == WorktreeState.ACTIVE.value
        assert row.unresolved_reason == _AC14_SENTENCE.format(worktree_path=row.worktree_path)
    assert list(INVARIANT.detect(session)) == []


def test_the_repair_of_nothing_clears_nothing(INVARIANT, session, live_park):
    assert INVARIANT.repair(session, []) == 0
    session.refresh(live_park)
    assert live_park.parked_at != ""
    assert live_park.state == WorktreeState.ACTIVE.value


def test_clear_vanished_parked_at_on_empty_input_returns_zero(session):
    from loregarden.services.stale_parked_at_rows import clear_vanished_parked_at

    assert clear_vanished_parked_at(session, []) == 0


def test_repair_skips_a_row_whose_parked_copy_is_present_at_repair_time(INVARIANT, session, stale):
    """AC14.3. Adapter re-detects; a PRESENT parked_at at repair time is skipped."""
    planted = [
        Violation(
            subject_id=stale.id,
            ticket_id=stale.ticket_id,
            entered_at=None,
            detail="handed, not detected",
        )
    ]
    Path(stale.parked_at).mkdir(parents=True)

    assert INVARIANT.repair(session, planted) == 0
    session.refresh(stale)
    assert stale.parked_at != ""
    assert stale.unresolved_reason == "stale parked locator"


def test_the_repair_sentence_is_its_own_and_not_ac7_or_ac13(INVARIANT, session, stale):
    """AC14.4. Verbatim equality with AC14, inequality with RESTORED and INTACT."""
    INVARIANT.repair(session, list(INVARIANT.detect(session)))
    session.refresh(stale)

    path = stale.worktree_path
    got = stale.unresolved_reason
    assert got == _AC14_SENTENCE.format(worktree_path=path)
    assert got != _AC7_RESTORED.format(path=path)
    assert got != _AC13_INTACT.format(path=path, clause="delete it again")
    assert "parked" in got
    assert "part of it may be gone" not in got
    assert "Nothing was deleted" not in got
    assert "git no longer lists" not in got
    assert "\u2014" in got
    assert stale.id not in got


def test_this_ticket_adds_no_migration():
    """AC14.1. Existing rows are none; 1026's column is already on this branch."""
    versions = Path(__file__).resolve().parents[1] / "loregarden" / "db" / "versions"
    hits = [
        path.name
        for path in versions.glob("*.py")
        if "lg-external-harness-1416" in path.read_text(encoding="utf-8")
    ]
    assert hits == [], f"this ticket writes no db/versions module: {hits}"


def test_the_ac14_sentence_is_not_a_refusal_sentence():
    """AC14.4. Constant lives in stale_parked_at_rows, not _REFUSAL_SENTENCE."""
    lifecycle = (
        Path(__file__).resolve().parents[1] / "loregarden" / "services" / "worktree_lifecycle.py"
    ).read_text(encoding="utf-8")
    assert "its parked copy is gone" not in lifecycle


def test_the_detector_module_imports_directory_state_and_does_not_reach_settle():
    """AC14.2 / AC14.3. Import from directory_release; do not wrap 1452's settle."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))
    imported: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("directory_release"):
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.Name):
            used.add(node.id)
        if isinstance(node, ast.alias):
            used.add(node.name)
        if isinstance(node, ast.Attribute):
            used.add(node.attr)
    assert {"directory_state", "DirectoryState"} <= imported
    assert not (used & _FORBIDDEN_LIFECYCLE_NAMES)


def _unreadable_child(tmp_path: Path, name: str) -> tuple[Path, str]:
    locked = tmp_path / "locked" / name
    inner = locked / "inner"
    inner.mkdir(parents=True)
    return locked, str(inner)


def test_an_unreadable_parked_at_is_kept_not_cleared(
    INVARIANT, session, workspace, tmp_path, caplog
):
    """AC14.2. OSError is not absence. exists() would call it gone and fire."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the mode bits this case is about")
    locked, parked_at = _unreadable_child(tmp_path, "park")
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "unreadable-park"),
        parked_at=parked_at,
        external_id="lg-unreadable-park",
    )
    locked.chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING):
            found = [v.subject_id for v in INVARIANT.detect(session)]
        assert found == []
        assert any(record.exc_info for record in caplog.records), (
            "OSError skip must log at warning with exc_info"
        )
        session.refresh(row)
        assert row.parked_at == parked_at
    finally:
        locked.chmod(stat.S_IRWXU)


def test_an_unreadable_worktree_path_is_kept_not_cleared(
    INVARIANT, session, workspace, tmp_path, caplog
):
    """AC14.2. OSError on either path skips. Log, keep the row."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the mode bits this case is about")
    locked, worktree_path = _unreadable_child(tmp_path, "checkout")
    row = _row(
        session,
        workspace,
        path=worktree_path,
        parked_at=_vanished(tmp_path, "unreadable-checkout.parked"),
        external_id="lg-unreadable-checkout",
    )
    locked.chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING):
            found = [v.subject_id for v in INVARIANT.detect(session)]
        assert found == []
        assert any(record.exc_info for record in caplog.records), (
            "OSError skip must log at warning with exc_info"
        )
        session.refresh(row)
        assert row.parked_at != ""
    finally:
        locked.chmod(stat.S_IRWXU)


# ---------------------------------------------------------------------------
# lg-external-harness-1416, test-break. Named mutants 1–5 and the edges the
# design suite only pins as a side effect of a happier fixture.
#
# Mutants (revert with sed, never `git checkout --`):
# 1. Repair calls settle_row_whose_checkout_is_gone — state becomes CLEANUP.
# 2. Detector uses os.path.exists — file-at-parked_at and OSError-skip die.
# 3. Detector keys on parked_at PRESENT — the live park copy is "repaired".
# 4. Census rewritten as a _record_refusal path test — a planted assignment
#    in another module still passes.
# 5. Repair writes parked_at="" but leaves unresolved_reason.
# ---------------------------------------------------------------------------

_CENSUS_FILE = Path(__file__).resolve().parent / "test_worktree_lifecycle.py"
_CENSUS_TEST = "test_every_writer_of_parked_at_sets_it_only_from_an_unrestored_release"
_EXISTS_PROBES = frozenset({"exists", "lexists", "is_dir", "isdir"})


def test_a_live_park_copy_is_left_alone(INVARIANT, session, live_park):
    """Mutant 3. The invariant *name* reads as the opposite of the bad state.

    A detector that fires when parked_at is PRESENT 'repairs' the only locator
    of a live park copy. live_park is a companion in the design tests; this
    one is the negative by itself, so a looser `stale.id in subject_ids`
    cannot hide it.
    """
    assert list(INVARIANT.detect(session)) == []
    assert INVARIANT.repair(session, list(INVARIANT.detect(session))) == 0
    session.refresh(live_park)
    assert live_park.parked_at != ""
    assert live_park.state == WorktreeState.ACTIVE.value
    assert Path(live_park.parked_at).is_dir()


@pytest.mark.parametrize("state", list(WorktreeState), ids=lambda s: s.value)
def test_every_worktree_state_with_a_vanished_parked_at_is_cleared_in_place(
    INVARIANT, session, workspace, tmp_path, state: WorktreeState
):
    """AC14.2 / mutant 1. No WorktreeState filter. CLEANUP was the design's
    one extra; an ACTIVE|CLEANUP select still misses MERGED and FAILED.

    Repair must not stamp CLEANUP over a live recovered checkout.
    """
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, f"state-{state.value}"),
        parked_at=_vanished(tmp_path, f"state-{state.value}.parked"),
        external_id=f"lg-state-{state.value}",
        state=state,
    )

    assert [v.subject_id for v in INVARIANT.detect(session)] == [row.id]
    assert INVARIANT.repair(session, list(INVARIANT.detect(session))) == 1
    session.refresh(row)
    assert row.state == state.value
    assert row.parked_at == ""
    assert row.unresolved_reason == _AC14_SENTENCE.format(worktree_path=row.worktree_path)


def test_a_file_at_worktree_path_is_not_this_invariant(INVARIANT, session, workspace, tmp_path):
    """Mutant 2, the other path. directory_state calls a file ABSENT.

    exists() on worktree_path would call it PRESENT, fire, and 'repair' a row
    whose checkout is not a directory — 1452's state, not this one.
    """
    stump = tmp_path / "checkouts" / "was-a-checkout"
    stump.parent.mkdir(parents=True, exist_ok=True)
    stump.write_text("not a checkout\n", encoding="utf-8")
    row = _row(
        session,
        workspace,
        path=str(stump),
        parked_at=_vanished(tmp_path, "file-checkout.parked"),
        external_id="lg-file-checkout",
    )

    assert list(INVARIANT.detect(session)) == []
    session.refresh(row)
    assert row.parked_at != ""


def test_both_paths_absent_is_not_this_invariant(INVARIANT, session, workspace, tmp_path):
    """PRESENT is required. Two vanished paths are not a recovered checkout."""
    row = _row(
        session,
        workspace,
        path=_vanished(tmp_path, "gone-checkout"),
        parked_at=_vanished(tmp_path, "gone-park"),
        external_id="lg-both-gone",
    )

    assert list(INVARIANT.detect(session)) == []
    session.refresh(row)
    assert row.parked_at != ""


def test_a_symlink_loop_at_parked_at_is_kept_not_cleared(
    INVARIANT, session, workspace, tmp_path, caplog
):
    """Mutant 2. ELOOP is an errno the kernel will not explain.

    exists() returns False and would fire. The chmod-unreadable case is
    EACCES; an implementer who special-cases that errno and uses exists()
    for the rest still treats a loop as absence.
    """
    park = tmp_path / "parks"
    park.mkdir()
    left = park / "loop-a"
    right = park / "loop-b"
    left.symlink_to(right)
    right.symlink_to(left)
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "loop-park"),
        parked_at=str(left),
        external_id="lg-loop-park",
    )

    with caplog.at_level(logging.WARNING):
        found = [v.subject_id for v in INVARIANT.detect(session)]
    assert found == []
    assert any(record.exc_info for record in caplog.records), (
        "OSError skip must log at warning with exc_info"
    )
    session.refresh(row)
    assert row.parked_at == str(left)


def test_a_symlink_to_a_park_directory_is_a_live_locator(INVARIANT, session, workspace, tmp_path):
    """os.stat follows the link; a directory at the other end is PRESENT."""
    real = Path(_vanished(tmp_path, "real-park"))
    real.mkdir(parents=True)
    link = tmp_path / "parks" / "linked-park"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "linked-park"),
        parked_at=str(link),
        external_id="lg-linked-park",
    )

    assert list(INVARIANT.detect(session)) == []
    session.refresh(row)
    assert row.parked_at == str(link)


def test_whitespace_parked_at_is_non_empty_and_is_a_violation(
    INVARIANT, session, workspace, tmp_path
):
    """# CHECKPOINT: spec selects non-empty parked_at. Do not strip.

    directory_state of whitespace is ABSENT; a PRESENT checkout makes this
    the bad state, not an empty locator.
    """
    row = _row(
        session,
        workspace,
        path=_checkout(tmp_path, "whitespace-park"),
        parked_at="   ",
        external_id="lg-whitespace-park",
    )

    assert [v.subject_id for v in INVARIANT.detect(session)] == [row.id]


def test_the_adapter_does_not_repair_rows_it_was_not_handed(INVARIANT, session, stale):
    """# CHECKPOINT: re-detect intersects the handed list, like 1452.

    Ignoring the list and sweeping every current violation would still
    converge, but `clear_vanished_parked_at(session, [])` returns 0, and
    empty input to the adapter must too.
    """
    assert INVARIANT.repair(session, []) == 0
    session.refresh(stale)
    assert stale.parked_at != ""
    assert stale.unresolved_reason == "stale parked locator"


def test_repair_writes_both_fields_before_it_commits(INVARIANT, session, stale):
    """Mutant 5 / R8. A commit that clears parked_at must already hold the sentence.

    Two commits — parked_at first, unresolved_reason later — leave a crash
    window where the locator is gone and the sentence is the old one.
    """
    snapshots: list[tuple[str, str]] = []
    inner = session.commit

    def _spy(*args: object, **kwargs: object) -> None:
        snapshots.append((stale.parked_at, stale.unresolved_reason))
        inner(*args, **kwargs)

    session.commit = _spy
    INVARIANT.repair(session, list(INVARIANT.detect(session)))
    sentence = _AC14_SENTENCE.format(worktree_path=stale.worktree_path)
    cleared = [(parked, reason) for parked, reason in snapshots if parked == ""]
    assert cleared, "repair must commit parked_at=''"
    assert all(reason == sentence for _, reason in cleared), (
        "a commit that clears parked_at must also carry the AC14 sentence"
    )


def test_the_detector_does_not_probe_with_exists():
    """Mutant 2. Absent is directory_state's answer, not exists()/is_dir()."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _EXISTS_PROBES:
            hits.append(node.attr)
        if isinstance(node, ast.Name) and node.id in _EXISTS_PROBES:
            hits.append(node.id)
        if isinstance(node, ast.alias) and node.name in _EXISTS_PROBES:
            hits.append(node.name)
    assert hits == [], f"detector must not probe with exists()/is_dir(): {hits}"


def test_the_clearer_interpolates_worktree_path_only():
    """AC14.4. Do not use `or worktree.id`. Path is PRESENT, so non-empty."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "clear_vanished_parked_at"
    )
    text = ast.unparse(fn)
    assert "worktree_path" in text
    assert "or worktree.id" not in text
    assert "or row.id" not in text


def test_the_refusal_sentence_table_gains_no_ac14_member():
    """AC14.4. Adding the sentence to _REFUSAL_SENTENCE would fire it on RESTORED."""
    from loregarden.services.directory_release import ReleaseDisposition
    from loregarden.services.worktree_lifecycle import _REFUSAL_SENTENCE

    assert set(_REFUSAL_SENTENCE) == {
        None,
        ReleaseDisposition.INTACT,
        ReleaseDisposition.STILL_PARKED,
        ReleaseDisposition.RESTORED,
    }
    assert all("parked copy is gone" not in sentence for sentence in _REFUSAL_SENTENCE.values())


def test_the_parked_at_census_is_an_ast_walk_not_a_path_test():
    """Mutant 4. A _record_refusal path test still passes with a planted
    `foo.parked_at = "x"` in another module.
    """
    src = _CENSUS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == _CENSUS_TEST
    )
    called: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    assert "_parked_at_stores" in called
    assert "_is_outcome_parked_path" in called
    assert "_is_empty_string" in called
    assert "_record_refusal" not in called
    assert "retire_worktree" not in called
    walker = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parked_at_stores"
    )
    walker_src = ast.unparse(walker)
    assert "ast.Assign" in walker_src
    assert "ast.AnnAssign" in walker_src
    assert "ast.AugAssign" in walker_src
    assert "db/versions/" in walker_src


def test_a_planted_parked_at_assignment_is_visible_to_the_census_walker():
    """Mutant 4. The walker the census uses must see `foo.parked_at = "x"`."""
    from tests.test_worktree_lifecycle import _enclosing_function, _parked_at_targets

    snippet = "def throwaway(foo):\n    foo.parked_at = 'x'\n"
    tree = ast.parse(snippet)
    hits: list[tuple[str | None, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(_parked_at_targets(target) for target in node.targets):
            hits.append((_enclosing_function(tree, node.lineno), ast.unparse(node.value)))
    assert hits == [("throwaway", "'x'")]


def test_the_live_path_writer_predicate_rejects_a_literal_and_an_empty_string():
    """AC14.5. `_record_refusal` copies `outcome.parked_path`, not a literal."""
    from tests.test_worktree_lifecycle import _is_empty_string, _is_outcome_parked_path

    literal = ast.parse("x.parked_at = 'parked'").body[0].value
    empty = ast.parse("x.parked_at = ''").body[0].value
    copied = ast.parse("x.parked_at = outcome.parked_path").body[0].value
    assert not _is_outcome_parked_path(literal)
    assert not _is_outcome_parked_path(empty)
    assert _is_outcome_parked_path(copied)
    assert _is_empty_string(empty)
    assert not _is_empty_string(copied)
    assert not _is_empty_string(literal)


def test_the_census_does_not_treat_a_comment_or_a_field_default_as_a_writer():
    """AC14.5. No text-grep (`baxter_chat_service.py:499` is a comment).
    Skip class-body AnnAssign (`git_tables.py:143`). Skip `db/versions/`.
    """
    from tests.test_worktree_lifecycle import _parked_at_stores

    relatives = {relative for relative, _func, _value in _parked_at_stores()}
    assert not any(path.startswith("db/versions/") for path in relatives)
    assert "models/domain/git_tables.py" not in relatives
    assert not any("baxter_chat_service" in path for path in relatives)
