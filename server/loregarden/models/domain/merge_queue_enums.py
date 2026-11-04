"""Vocabulary for the merge queue (lg-milestone-that-967).

Split out of `enums` (near its size cap), the same way `pull_request_enums` was.
"""

from __future__ import annotations

from enum import StrEnum


class MergeQueueState(StrEnum):
    """Where one candidate for the base branch is in the merge queue (lg-milestone-that-968).

    ``QUEUED`` waits its turn; one entry per workspace is past it at a time.
    ``STAGING`` is cutting the merged result on the base's current tip,
    ``TESTING`` waits for the required checks on that staged commit, and
    ``MERGING`` is landing it. The last four are terminal: ``TIMED_OUT`` is a
    wait that expired, kept apart from ``FAILED`` so an abandoned entry is
    never mistaken for one that was judged.
    """

    QUEUED = "queued"
    STAGING = "staging"
    TESTING = "testing"
    MERGING = "merging"
    MERGED = "merged"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    WITHDRAWN = "withdrawn"


#: States that hold the queue's one active slot.
MERGE_QUEUE_ACTIVE_STATES: frozenset[MergeQueueState] = frozenset(
    {MergeQueueState.STAGING, MergeQueueState.TESTING, MergeQueueState.MERGING}
)
#: States an entry never leaves on its own; a retry re-queues it.
MERGE_QUEUE_TERMINAL_STATES: frozenset[MergeQueueState] = frozenset(
    {
        MergeQueueState.MERGED,
        MergeQueueState.FAILED,
        MergeQueueState.TIMED_OUT,
        MergeQueueState.WITHDRAWN,
    }
)


class MergeQueueWait(StrEnum):
    """Why an entry that is not moving is not moving, as the operator reads it.

    ``CHECKS_UNREPORTED`` and ``GITHUB_UNREACHABLE`` are the distinction
    `ci_checks` keeps by raising: "nothing has reported on the staged commit
    yet" is a wait, "gh could not be asked" is a failure to know, and the two
    must never read alike (lg-milestone-that-969).
    """

    NONE = ""
    #: Behind the active entry.
    BEHIND = "behind"
    #: A required check has not reported on the staged commit at all.
    CHECKS_UNREPORTED = "checks_unreported"
    #: Every required check has reported; at least one is still running.
    CHECKS_RUNNING = "checks_running"
    GITHUB_UNREACHABLE = "github_unreachable"
    #: The base moved after staging; the staged commit was discarded.
    BASE_MOVED = "base_moved"
    #: Failed on a conflict only a person can resolve; the artifact is open.
    CONFLICT_ARTIFACT_OPEN = "conflict_artifact_open"


class MergeQueueAdmission(StrEnum):
    """Why an enqueue was or was not admitted. Never a bare ``False``."""

    ADMITTED = "admitted"
    #: The entry for this pull request took the new head in place.
    UPDATED = "updated"
    NO_PULL_REQUEST = "no_pull_request"
    DUPLICATE_HEAD = "duplicate_head"
    NO_TICKET = "no_ticket"


class MergeQueueAction(StrEnum):
    """What `loregarden_dequeue_merge` does to an entry."""

    #: Take it out of the queue; refused once it is merging or finished.
    WITHDRAW = "withdraw"
    #: Send a failed, timed-out or withdrawn entry to the back of the queue.
    RETRY = "retry"

    @classmethod
    def try_parse(cls, name: str) -> MergeQueueAction | None:
        try:
            return cls(name)
        except (
            ValueError
        ):  # silent-ok: None is the caller's "unknown action" answer, which it raises on
            return None
