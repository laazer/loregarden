"""A queued merge-queue entry's "behind PR #N" label names the head it is behind now.

Every entry waiting its turn is labelled `MergeQueueWait.BEHIND` with the head
entry's PR. The label used to be written once, the first time an entry was
marked behind, and never again: `_mark_behind` skipped any entry already
labelled BEHIND. So when #980 merged and #987 became the head, the entries
behind it still read "behind PR #980", and later "behind PR #985" after #985
had failed (lg-milestone-that-2808). The queue was moving; the page said it
was not.

`relabel_behind` is the one writer of the label, and it rewrites whatever no
longer matches, every cycle. The invariant catches a label that survives a
head change by more than a CI tick, which only a cycle that stopped relabelling
can produce.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from loregarden.config import settings
from loregarden.models.domain import (
    MERGE_QUEUE_TERMINAL_STATES,
    InvariantReentry,
    MergeQueueEntry,
    MergeQueueState,
    MergeQueueWait,
    comparable_utc,
    utcnow,
)
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.merge_queue import head_entry, live_entries, transition
from sqlmodel import Session, col, select

#: A head change has this long to reach every label: two poll ticks and a margin.
_GRACE_MARGIN_SECONDS = 60.0


def behind_detail(head: MergeQueueEntry) -> str:
    """What an entry waiting behind ``head`` says."""
    return f"behind PR #{head.pr_number}"


def _stale_behind(session: Session, workspace_id: str) -> list[tuple[MergeQueueEntry, str]]:
    """Each queued entry behind the head whose label is not the head's, with the right one."""
    head = head_entry(session, workspace_id)
    if head is None:
        return []
    want = behind_detail(head)
    return [
        (entry, want)
        for entry in live_entries(session, workspace_id)
        if entry.id != head.id
        and entry.state is MergeQueueState.QUEUED
        and (entry.wait is not MergeQueueWait.BEHIND or entry.detail != want)
    ]


def relabel_behind(session: Session, workspace_id: str) -> int:
    """Label every queued entry behind the head with that head; how many changed."""
    changed = 0
    for entry, want in _stale_behind(session, workspace_id):
        # A label only: an entry withdrawn or re-pointed meanwhile matches
        # nothing, and the next cycle labels whatever is still queued.
        if transition(
            session, entry, MergeQueueState.QUEUED, wait=MergeQueueWait.BEHIND, detail=want
        ):
            changed += 1
    return changed


def _head_changed_at(session: Session, workspace_id: str, head: MergeQueueEntry) -> datetime:
    """The latest moment the head could have changed: an entry finishing, or the head moving."""
    finished = session.exec(
        select(MergeQueueEntry.state_changed_at)
        .where(MergeQueueEntry.workspace_id == workspace_id)
        .where(col(MergeQueueEntry.state).in_(list(MERGE_QUEUE_TERMINAL_STATES)))
        .order_by(col(MergeQueueEntry.state_changed_at).desc())
        .limit(1)
    ).first()
    moments = [comparable_utc(head.state_changed_at)]
    if finished is not None:
        moments.append(comparable_utc(finished))
    return max(moments)


def _grace() -> timedelta:
    return timedelta(seconds=2 * settings.ci_poll_tick_seconds + _GRACE_MARGIN_SECONDS)


def stale_behind_labels(session: Session) -> list[Violation]:
    """Queued entries labelled BEHIND a PR that is not the head, past one cycle's grace."""
    workspace_ids = session.exec(
        select(MergeQueueEntry.workspace_id)
        .where(MergeQueueEntry.state == MergeQueueState.QUEUED)
        .where(MergeQueueEntry.wait == MergeQueueWait.BEHIND)
        .distinct()
    ).all()
    violations: list[Violation] = []
    now = utcnow()
    for workspace_id in workspace_ids:
        head = head_entry(session, workspace_id)
        if head is None:
            continue  # silent-ok: a BEHIND row is live, so a head exists; nothing to compare
        entered_at = _head_changed_at(session, workspace_id, head) + _grace()
        if comparable_utc(now) < entered_at:
            continue  # silent-ok: the head changed within a cycle; the next cycle relabels
        for entry, want in _stale_behind(session, workspace_id):
            if entry.wait is not MergeQueueWait.BEHIND:
                continue  # silent-ok: not yet labelled at all is "waiting its turn", still true
            violations.append(
                Violation(
                    subject_id=entry.id,
                    ticket_id=entry.ticket_id,
                    entered_at=entered_at,
                    detail=f"PR #{entry.pr_number} reads {entry.detail!r}; the head is {want}",
                )
            )
    return violations


def repair_stale_behind_labels(session: Session, violations: Sequence[Violation]) -> int:
    workspace_ids: set[str] = set()
    for violation in violations:
        entry = session.get(MergeQueueEntry, violation.subject_id)
        if entry is not None:
            workspace_ids.add(entry.workspace_id)
    return sum(relabel_behind(session, workspace_id) for workspace_id in sorted(workspace_ids))


QUEUED_ENTRIES_NAME_THE_CURRENT_HEAD = Invariant(
    name="queued_entries_name_the_current_head",
    ticket="lg-milestone-that-2808",
    statement="every queued merge-queue entry labelled behind names the entry now at the head",
    detect=stale_behind_labels,
    repair=repair_stale_behind_labels,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 26),
    prevented_by=(
        "server/tests/test_merge_queue_behind.py::"
        "test_every_writer_of_the_behind_label_follows_a_new_head"
    ),
)
