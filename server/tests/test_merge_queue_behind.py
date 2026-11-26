"""A queued entry's "behind PR #N" names the head it is behind now (lg-milestone-that-2808).

The label was written once and never moved: entries behind #987 still read
"behind PR #980" after #980 had merged.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    MergeQueueEntry,
    MergeQueueState,
    MergeQueueWait,
    Workspace,
    utcnow,
)
from loregarden.services import merge_queue_behind
from loregarden.services.invariants import run_invariant
from loregarden.services.merge_queue_behind import (
    QUEUED_ENTRIES_NAME_THE_CURRENT_HEAD,
    relabel_behind,
)
from loregarden.services.merge_queue_loop import MergeQueueLoop
from sqlmodel import Session
from tests.factories import make_workspace

_PACKAGE = Path(merge_queue_behind.__file__).resolve().parent.parent


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session) -> Workspace:
    return make_workspace(db_session, slug="queue-behind")


def _entry(
    session: Session,
    workspace: Workspace,
    pr: int,
    state: MergeQueueState,
    *,
    wait: MergeQueueWait = MergeQueueWait.NONE,
    detail: str = "waiting its turn",
    changed_ago: timedelta = timedelta(0),
) -> MergeQueueEntry:
    entry = MergeQueueEntry(
        workspace_id=workspace.id,
        pr_number=pr,
        pr_url=f"https://github.com/o/r/pull/{pr}",
        pr_head_branch=f"claude/pr-{pr}",
        source_branch=f"claude/pr-{pr}",
        head_sha=f"{pr:040x}",
        position=pr,
        state=state,
        wait=wait,
        detail=detail,
        state_changed_at=utcnow() - changed_ago,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _labels(session: Session, *entries: MergeQueueEntry) -> list[tuple[MergeQueueWait, str]]:
    for entry in entries:
        session.refresh(entry)
    return [(entry.wait, entry.detail) for entry in entries]


def _today_after_980_merged(session: Session, workspace: Workspace, ago: timedelta):
    """#980 merged, #987 testing, two entries still labelled behind #980."""
    _entry(session, workspace, 980, MergeQueueState.MERGED, changed_ago=ago)
    head = _entry(session, workspace, 987, MergeQueueState.TESTING, changed_ago=ago)
    stale = [
        _entry(
            session,
            workspace,
            pr,
            MergeQueueState.QUEUED,
            wait=MergeQueueWait.BEHIND,
            detail="behind PR #980",
            changed_ago=ago,
        )
        for pr in (988, 989)
    ]
    return head, stale


def _writers_of_the_behind_label() -> set[str]:
    """Every module under the package that writes ``wait=MergeQueueWait.BEHIND``."""
    pattern = re.compile(r"wait\s*=\s*MergeQueueWait\.BEHIND\b")
    return {
        path.relative_to(_PACKAGE).as_posix()
        for path in _PACKAGE.rglob("*.py")
        if pattern.search(path.read_text())
    }


def test_every_writer_of_the_behind_label_follows_a_new_head(
    db_session: Session, workspace: Workspace
):
    # One writer: `relabel_behind`. A second would have to keep the label in step too.
    assert _writers_of_the_behind_label() == {"services/merge_queue_behind.py"}
    _, stale = _today_after_980_merged(db_session, workspace, timedelta(seconds=5))

    # The loop's cycle is how the label is written; the head waiting on its checks
    # is enough, so the step itself is left out.
    loop = MergeQueueLoop(db_session, workspace, github=mock.Mock())
    with mock.patch.object(MergeQueueLoop, "_step", return_value=False):
        loop.advance()

    assert _labels(db_session, *stale) == [(MergeQueueWait.BEHIND, "behind PR #987")] * 2


def test_the_label_moves_again_when_the_head_fails_in_the_same_cycle(
    db_session: Session, workspace: Workspace
):
    head = _entry(db_session, workspace, 985, MergeQueueState.TESTING)
    behind = _entry(
        db_session,
        workspace,
        986,
        MergeQueueState.QUEUED,
        wait=MergeQueueWait.BEHIND,
        detail="behind PR #985",
    )
    last = _entry(
        db_session,
        workspace,
        990,
        MergeQueueState.QUEUED,
        wait=MergeQueueWait.BEHIND,
        detail="behind PR #985",
    )

    def step(loop: MergeQueueLoop, entry: MergeQueueEntry) -> bool:
        if entry.id == head.id:
            entry.state = MergeQueueState.FAILED
            loop.session.add(entry)
            loop.session.commit()
            return True
        entry.state = MergeQueueState.TESTING  # #986 staged and is now waiting on its checks
        loop.session.add(entry)
        loop.session.commit()
        return False

    with mock.patch.object(MergeQueueLoop, "_step", autospec=True, side_effect=step):
        MergeQueueLoop(db_session, workspace, github=mock.Mock()).advance()

    db_session.refresh(behind)
    assert behind.state is MergeQueueState.TESTING
    assert _labels(db_session, last) == [(MergeQueueWait.BEHIND, "behind PR #986")]


def test_relabel_leaves_the_head_and_a_right_label_alone(db_session: Session, workspace: Workspace):
    head = _entry(db_session, workspace, 7, MergeQueueState.QUEUED, detail="waiting its turn")
    right = _entry(
        db_session,
        workspace,
        8,
        MergeQueueState.QUEUED,
        wait=MergeQueueWait.BEHIND,
        detail="behind PR #7",
    )
    fresh = _entry(db_session, workspace, 9, MergeQueueState.QUEUED)

    assert relabel_behind(db_session, workspace.id) == 1
    assert _labels(db_session, head, right, fresh) == [
        (MergeQueueWait.NONE, "waiting its turn"),
        (MergeQueueWait.BEHIND, "behind PR #7"),
        (MergeQueueWait.BEHIND, "behind PR #7"),
    ]


def test_a_stale_label_past_a_cycle_is_found_and_repaired(
    db_session: Session, workspace: Workspace
):
    _, stale = _today_after_980_merged(db_session, workspace, timedelta(hours=1))

    check = run_invariant(db_session, QUEUED_ENTRIES_NAME_THE_CURRENT_HEAD)

    assert (check.found, check.repaired) == (2, 2)
    assert _labels(db_session, *stale) == [(MergeQueueWait.BEHIND, "behind PR #987")] * 2
    assert run_invariant(db_session, QUEUED_ENTRIES_NAME_THE_CURRENT_HEAD).found == 0


def test_a_head_that_changed_within_a_cycle_is_not_yet_a_violation(
    db_session: Session, workspace: Workspace
):
    _today_after_980_merged(db_session, workspace, timedelta(seconds=10))

    assert run_invariant(db_session, QUEUED_ENTRIES_NAME_THE_CURRENT_HEAD).found == 0
