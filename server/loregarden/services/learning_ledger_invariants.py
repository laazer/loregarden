"""Invariants over the learning ledger: graded when a run concludes, counted as delivered.

Both states were real. From 2026-10-03 to 2026-11-23 nothing was graded, because
grading took the oldest 200 ungraded rows and all 200 were runs still open. And
from 2026-10-08 a briefing whose learnings were cut to fit recorded them as injected
anyway, so the one column that could have shown the loss reported none.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from loregarden.models.domain import InvariantReentry, LearningApplication, MemoryBriefing
from loregarden.services.invariants import Invariant, Violation
from loregarden.services.learning_outcomes import gradable_runs, settle_runs
from sqlalchemy import func
from sqlmodel import Session, col, select

#: Learnings were first linked to runs on 2026-09-28, mid-day. A briefing from before
#: has no links to count against, so its figure cannot be checked or corrected.
LINKED_SINCE = datetime(2026, 9, 29)  # noqa: DTZ001 - stored naive UTC
#: Briefings corrected per pass; the backlog at registration was 1,072.
_COUNT_BATCH = 500


def ungraded_concluded_runs(session: Session) -> list[Violation]:
    """Concluded runs with ungraded learnings, one pass's worth.

    `entered_at` is when the run finished; its window closes later than that.
    """
    return [
        Violation(
            subject_id=run.id,
            ticket_id=run.ticket_id,
            entered_at=run.finished_at,
            detail=f"{run.run_code} concluded with ungraded learnings",
        )
        for run, _ in gradable_runs(session)
    ]


def grade_runs(session: Session, violations: Sequence[Violation]) -> int:
    settled = settle_runs(session, [v.subject_id for v in violations])
    session.commit()
    return settled


def _linked_count():
    return (
        select(func.count(col(LearningApplication.id)))
        .where(LearningApplication.run_id == MemoryBriefing.run_id)
        .correlate(MemoryBriefing)
        .scalar_subquery()
    )


def overcounted_briefings(session: Session) -> list[Violation]:
    """Briefings claiming more learnings than were linked to their run.

    Linked per run rather than per briefing: a second assembly for one run links
    nothing new, so a briefing's own links can legitimately be none.
    """
    linked = _linked_count()
    rows = session.exec(
        select(MemoryBriefing, linked)
        .where(col(MemoryBriefing.created_at) >= LINKED_SINCE)
        .where(col(MemoryBriefing.learnings_injected) > linked)
        .order_by(col(MemoryBriefing.created_at))
        .limit(_COUNT_BATCH)
    ).all()
    return [
        Violation(
            subject_id=row.id,
            ticket_id=row.ticket_id,
            entered_at=row.created_at,
            detail=f"briefing {row.id} claims {row.learnings_injected} learnings, {count} linked",
        )
        for row, count in rows
    ]


def correct_counts(session: Session, violations: Sequence[Violation]) -> int:
    corrected = 0
    for violation in violations:
        row = session.get(MemoryBriefing, violation.subject_id)
        if row is None:
            continue
        linked = session.exec(
            select(func.count(col(LearningApplication.id))).where(
                LearningApplication.run_id == row.run_id
            )
        ).one()
        if row.learnings_injected <= linked:
            continue
        row.learnings_injected = linked
        session.add(row)
        corrected += 1
    session.commit()
    return corrected


CONCLUDED_RUNS_GRADE_THEIR_LEARNINGS = Invariant(
    name="concluded_runs_grade_their_learnings",
    ticket="lg-improved-memory-2772",
    statement="no learning surfaced into a concluded run stays ungraded",
    detect=ungraded_concluded_runs,
    repair=grade_runs,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 25),
    expected_because=(
        "a run concludes when its stage re-runs, its ticket finishes or it blocks; none of "
        "those writers grades learnings, so every grading starts here"
    ),
    prevented_by=(
        "server/tests/test_learning_outcomes.py"
        "::test_runs_that_cannot_be_graded_never_hold_back_those_that_can"
    ),
)

BRIEFINGS_COUNT_WHAT_THEY_DELIVERED = Invariant(
    name="briefings_count_what_they_delivered",
    ticket="lg-improved-memory-2772",
    statement="no briefing claims more learnings than reached its run's prompt",
    detect=overcounted_briefings,
    repair=correct_counts,
    reentry=InvariantReentry.DEFECT,
    # After the fix was written: the live server overcounts until it restarts on it.
    since=date(2026, 11, 25),
    prevented_by=(
        "server/tests/test_inherited_wisdom_result.py"
        "::test_the_counts_are_what_the_text_holds_at_every_cap"
    ),
)
