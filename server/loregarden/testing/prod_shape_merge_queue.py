"""The scenario's merge queue: what a workspace dashboard shows landing on main.

**Re-measured 2026-11-08 (read-only):** live holds 7 entries, all merged, all a
tree's (one milestone's PRs), against about 49 pull requests merged to `main`
since the queue shipped on 2026-11-03. Interactive sessions' PRs now enter it
by number with no ticket (`merge_queue_ticketless_entries`), so most entries
will be ticketless: about 7 in 49 were trees. The scenario keeps one entry in
``TREE_EVERY`` a tree's, so a ticket's row (title, external id, conflict
artifact) and a session's row (branch name, no ticket) both exist on every
surface; the rest are ticketless.

Depth is the queue's own word, not yet data: "typically zero or one". At about
ten PRs a day, each in line for 15-30 minutes (the 7 live entries took 15-33),
a few at once is the realistic peak, so the scenario draws a head waiting on its
checks with two behind it, one of each way an entry stops, and the recent
merges. Re-measure depth once interactive PRs have used the queue for a week.

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


MERGE_QUEUE_CALIBRATION = MergeQueueShape(measured="2026-11-08 (live, read-only)", live=3, merged=8)

#: One entry in this many is a tree's; the rest are PRs admitted by number.
TREE_EVERY = 3

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
        pr = 700 + n
        changed = now - timedelta(minutes=minutes_ago)
        if n % TREE_EVERY == 0:
            ticket_id, external_id = roots[n % len(roots)]
            head_branch, source = f"publish/integration/{external_id}", f"integration/{external_id}"
        else:
            ticket_id = None
            head_branch = source = f"claude/session-{n:02x}{pr:x}"
        return MergeQueueEntry(
            id=shape_id("merge-queue", n),
            workspace_id=workspace_id,
            ticket_id=ticket_id,
            pr_number=pr,
            pr_url=f"https://github.com/laazer/loregarden/pull/{pr}",
            pr_head_branch=head_branch,
            source_branch=source,
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
