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
