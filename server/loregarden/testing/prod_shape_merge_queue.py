"""The scenario's merge queue: what a workspace dashboard shows landing on main.

**Not measured from live.** The queue ships with this table, so live holds no
rows yet (checked read-only 2026-11-03: `merge_queue_entries` has 0 rows). The shape is the volume the queue's own ticket states
(lg-milestone-that-972): "typically zero or one, a milestone's worth at most".
An entry is a tree root's PR, so at most is the number of roots publishing at
once. The scenario draws the largest realistic queue — a head waiting on its
checks, two behind it — plus one of each way an entry stops and the recent
merges, so every row shape the surface renders exists. Re-measure once the
queue has run for a few weeks; until then this is the ticket's word, not data.

Split from `prod_shape` (near its size cap); `build_prod_shape` calls it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta

from pydantic import BaseModel
from sqlmodel import Session

from loregarden.models.domain import MergeQueueEntry, MergeQueueState, MergeQueueWait, utcnow


class MergeQueueShape(BaseModel):
    measured: str
    #: Live, in queue order: the head first.
    live: int
    #: Finished and kept for the list: merged, failed, timed out, withdrawn.
    merged: int


MERGE_QUEUE_CALIBRATION = MergeQueueShape(measured="2026-11-03 (ticket volume)", live=3, merged=8)

#: The workspace whose dashboard the fixture records.
MERGE_QUEUE_WORKSPACE = "loregarden"

_LOG_TAIL = "\n".join(
    [
        "FAILED tests/test_publish_tree.py::test_a_tree_publishes_its_cut - AssertionError",
        "E   assert 'f79660f84d98' == '6d0ca8adfe03'",
        "E     - 6d0ca8adfe03",
        "E     + f79660f84d98",
        "=========== 1 failed, 4127 passed, 31 skipped in 512.40s ===========",
    ]
)

_CHECKS_RUNNING = [
    {"name": "Client (TypeScript)", "outcome": "pending", "url": ""},
    {"name": "PR UX section", "outcome": "passing", "url": ""},
    {"name": "PR fix coverage", "outcome": "passing", "url": ""},
    {"name": "Server (Python)", "outcome": "pending", "url": ""},
]


def add_merge_queue_shape(
    session: Session,
    workspace_id: str,
    roots: list[tuple[str, str]],
    shape_id: Callable[[str, int | str], str],
) -> int:
    """Queue entries over ``roots`` (ticket id, external id), oldest first. Returns the count."""
    now = utcnow()
    rows: list[MergeQueueEntry] = []
    position = 0

    def entry(n: int, state: MergeQueueState, minutes_ago: int, **fields) -> MergeQueueEntry:
        nonlocal position
        position += 1
        ticket_id, external_id = roots[n % len(roots)]
        pr = 700 + n
        changed = now - timedelta(minutes=minutes_ago)
        return MergeQueueEntry(
            id=shape_id("merge-queue", n),
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            pr_number=pr,
            pr_url=f"https://github.com/laazer/loregarden/pull/{pr}",
            pr_head_branch=f"publish/integration/{external_id}",
            source_branch=f"integration/{external_id}",
            head_sha=f"{n:02x}" * 20,
            position=position,
            state=state,
            enqueued_at=changed - timedelta(minutes=20),
            state_changed_at=changed,
            **fields,
        )

    for n in range(MERGE_QUEUE_CALIBRATION.merged):
        rows.append(
            entry(
                n,
                MergeQueueState.MERGED,
                60 * 24 * (MERGE_QUEUE_CALIBRATION.merged - n),
                staged_sha=f"{n:02x}" * 19 + "aa",
                staged_tree=f"{n:02x}" * 19 + "bb",
                merge_commit=f"{n:02x}" * 19 + "cc",
                landed_tree=f"{n:02x}" * 19 + "bb",
                attempts=1,
                detail="landed the staged tree",
            )
        )
    stopped = MERGE_QUEUE_CALIBRATION.merged
    rows.append(
        entry(
            stopped,
            MergeQueueState.FAILED,
            300,
            attempts=1,
            detail="required check failed on the staged commit: Server (Python)",
            log_tail=_LOG_TAIL,
            checks_json=json.dumps([{"name": "Server (Python)", "outcome": "failing", "url": ""}]),
        )
    )
    rows.append(
        entry(
            stopped + 1,
            MergeQueueState.FAILED,
            180,
            wait=MergeQueueWait.CONFLICT_ARTIFACT_OPEN,
            detail="conflicts with main 1a2b3c4d5e6f in server/loregarden/services/publish_tree.py",
            conflicted_files_json=json.dumps(["server/loregarden/services/publish_tree.py"]),
            artifact_ref=roots[0][1] + "-conflict",
        )
    )
    rows.append(
        entry(
            stopped + 2,
            MergeQueueState.WITHDRAWN,
            90,
            detail="withdrawn: superseded by a new landing",
        )
    )
    live = stopped + 3
    rows.append(
        entry(
            live,
            MergeQueueState.TESTING,
            12,
            wait=MergeQueueWait.CHECKS_RUNNING,
            staged_sha="ee" * 20,
            staged_tree="ef" * 20,
            staged_base_sha="ed" * 20,
            attempts=1,
            checks_json=json.dumps(_CHECKS_RUNNING),
            testing_started_at=now - timedelta(minutes=12),
        )
    )
    for n in range(live + 1, live + MERGE_QUEUE_CALIBRATION.live):
        rows.append(entry(n, MergeQueueState.QUEUED, 5, wait=MergeQueueWait.BEHIND))
    for row in rows:
        session.merge(row)
    session.commit()
    return len(rows)
