"""Why a ticket is blocked, in a vocabulary that says who can unblock it.

Every block used to collapse into one state whose only reader was a person:
a lease expiry, a test the agent could not crack, and a spec bar that tripped
all looked the same, and each cost the same three turns — ask what happened,
ask for the fix, requeue by hand (lg-workflow-integrity-749). The kind is what
lets the control plane act on the first two itself and turn the third into a
question with options.
"""

from __future__ import annotations

from enum import StrEnum


class BlockKind(StrEnum):
    """Who can unblock this. Closed: a member added here is a policy added
    in `services.block_classification` and a reader change in the client."""

    #: The control plane or the environment failed, not the work: a lease
    #: expired, the parent was reaped, no report was parsed, preflight failed,
    #: a gate tool crashed. Classified by the control plane itself — it wrote
    #: the failure — and fixable without a person.
    HARNESS = "harness"
    #: The agent stopped on something an agent can still do: a failing test it
    #: could not crack, a gap it could have assumed past, a missing dependency.
    #: A repair run's territory (750).
    WORK = "work"
    #: A choice only a person should make — a spec bar tripped, two valid
    #: approaches, an ambiguous criterion. Becomes an approval WITH OPTIONS;
    #: the answer is a checkpoint and the ticket requeues itself.
    DECISION = "decision"
    #: A person's hands are needed: credentials, hardware, an external
    #: account. The existing human-action ladder (460) handles it.
    HUMAN_ACTION = "human_action"
    #: Another ticket has to move first; nothing is owed on this one. Written
    #: only by the rollup (`services.ticket_rollup`), on a parent that is
    #: blocked because a child is. Never a stage report's word: an agent that
    #: names it names no ticket, and the badge would point nowhere.
    TICKETS = "tickets"
    #: The ticket's branch cannot take its target: work that landed before it
    #: collides with its own. Not an agent's stage to repair (re-running the
    #: stage hits the same checkout); the merge turn (`branch_takes_target`) is
    #: how it clears, and a person resolves it once that turn's attempts are spent.
    CONFLICT = "conflict"


class BlockedOn(StrEnum):
    """What a stuck ticket is waiting for, as the operator sorts it — derived.

    `BlockKind` is the stored, fine-grained cause on a blocked ticket. This is
    the coarser question the board and the filter ask, and it also covers a
    backlog ticket held by an unmet prerequisite, which is never `blocked`
    (`services.blocked_on`).
    """

    #: A person must act: a decision, a human action, or a block nothing has
    #: classified yet.
    PERSON = "person"
    #: Another ticket must move first: a blocked child, or an unmet prerequisite.
    TICKETS = "tickets"
    #: The control plane or a repair run can clear it (`harness`, `work`).
    AGENT = "agent"


class WaitingReason(StrEnum):
    """Why a ticket waits on another one, for the "Waiting on" line."""

    #: A blocked descendant, which rolled this parent up to blocked.
    BLOCKED_CHILD = "blocked_child"
    #: A prerequisite (`ticket_dependencies`) that has not finished.
    PREREQUISITE = "prerequisite"
