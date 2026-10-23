"""The exact sentences the control plane writes when it stops its own agent.

A leaf module on purpose. These strings are matched exactly in several places —
`run_interruption.blocked_by_interruption` compares `blocking_issues` against
them to decide a ticket may be resumed unprompted, `workflow_monitor` reads the
same set to decide a stage is settleable, and `stage_report.is_transient_failure`
looks for them in a dead run's stderr to tell a reload apart from a rejection.

They used to live in `run_interruption`, which imports `triage_service` for one
agent id and through it most of the executor stack. The classifier could not
reach them from there without closing a cycle, and a second copy of a string
whose whole job is to be compared exactly is the drift nobody notices: one half
of the system stops recognising a message the other half still writes. So the
constants moved down here, where anything may import them, and
`run_interruption` re-exports them for its existing callers.
"""

from __future__ import annotations

INTERRUPTED_RUN_MESSAGE = (
    "Agent run interrupted before completion (server reload or worker stopped). "
    "Re-run the stage to continue."
)

SUPERSEDED_RUN_MESSAGE = (
    "Agent run superseded by a fresh checkout of the same stage. Nothing "
    "restarted — a later attempt claimed the stage while this run still held it."
)

STRANDED_STAGE_MESSAGE = (
    "Stage was left running with no agent run behind it (the run ended before its "
    "stage was settled). Re-run the stage to continue."
)

ORPHAN_OF_TERMINAL_ORCH_MESSAGE = (
    "Parent orchestration is already terminal; this run was left in flight."
)

#: How `dispatch_guard` refuses to start a stage under an orchestration that
#: is already over. The full sentence names the run and its status, so the
#: classifier matches this prefix. It is the control plane declining to spend
#: a run, not the agent failing: a block carrying it is HARNESS — the stage's
#: work stands and the ticket wants a resume, never a repair turn or a person
#: (lg-workflow-integrity-758).
DISPATCH_REFUSED_TERMINAL_PARENT_PREFIX = "Cannot start run: orchestration"

#: Messages that mark a ticket as blocked by an *artifact* of this process
#: rather than by a real failure, so recovery may re-run the stage unprompted.
#: ``SUPERSEDED_RUN_MESSAGE`` is deliberately absent: a superseded run is
#: replaced in the same breath by the checkout that claimed its stage, which
#: clears the blocking text on its way to RUNNING. There is nothing left for
#: recovery to resume, and treating it as resumable would re-dispatch a stage
#: somebody is already holding.
INTERRUPTION_MESSAGES = frozenset({INTERRUPTED_RUN_MESSAGE, STRANDED_STAGE_MESSAGE})

#: Every message whose recovery some other path already owns: the boot sweep
#: resumes the first two (`orchestration_recovery`) and the workflow monitor
#: settles all three. Anything reading a dead run's stderr to decide what to do
#: next must defer to them — `stage_transient_retry` declines these outright,
#: because re-arming a stage to PENDING at boot, where no driver is running,
#: replaces a recovery that works with a ticket that merely looks busy.
CONTROL_PLANE_DEATH_MESSAGES = frozenset(INTERRUPTION_MESSAGES | {ORPHAN_OF_TERMINAL_ORCH_MESSAGE})


#: The one phrase every "a restart killed this" message shares. Five modules
#: write such a message — the four chat surfaces and the scoper — each as its own
#: f-string, and a classifier reading a dead run's output had heard of none of
#: them: only the stage-level `INTERRUPTED_RUN_MESSAGE` above was ever matched.
#: The spellings were found by running every distinct `stderr` in `agent_runs`
#: past `is_transient_failure` rather than by reading the code, which is the only
#: way a missing spelling shows up at all.
#:
#: Matching on this marker rather than on each full sentence is what makes a
#: sixth surface safe: a new message built by `restart_interruption_message` is
#: recognised the day it is written, and one that is not is at least recognised
#: by the phrase it inevitably reuses.
RESTART_INTERRUPTION_MARKER = "was interrupted by a server restart"


def restart_interruption_message(subject: str, follow_up: str) -> str:
    """ "<subject> was interrupted by a server restart and <follow_up>"

    ``subject`` names who was interrupted as the reader knows them ("Baxter",
    "The scoper"); ``follow_up`` says what they should do, in that surface's own
    terms, because "send the message again" and "run it again" are different
    actions on different screens.
    """
    return f"{subject} {RESTART_INTERRUPTION_MARKER} and {follow_up}"


def is_restart_interruption(*texts: str) -> bool:
    """Whether any of these strings reports a run this server restart killed."""
    return any(RESTART_INTERRUPTION_MARKER in (text or "").lower() for text in texts)


#: The opening of the sweep sentence written when an orchestration's lease
#: expires. A prefix rather than the whole message because the rest names the
#: run and how much of its ticket subtree the predicate examined;
#: `block_classification` matches on this part so the block keeps classifying
#: as HARNESS whatever the count renders as.
ORCHESTRATION_LEASE_EXPIRED_PREFIX = "Orchestration lease expired"


def expired_lease_message(run_code: str, subtree_orchestrations: int) -> str:
    """What the lease sweep says when it reaps a run, naming what it examined.

    The predicate behind this sentence no longer asks only about agent runs
    under ``run_code``: it walks the run's ticket subtree and asks about every
    orchestration beneath it too (``run_concurrency.subtree_orchestration_ids``).
    So the message has to answer the operator's actual question — was this
    run's driver dead, or was it waiting on work one level down? — which it
    can only do by naming the breadth of the walk.

    A *count*, never a list: a parent ticket's subtree holds 18 orchestration
    runs on average and 209 at the largest (measured 2026-10-20), and this text
    is rendered as a block banner whose teaser is 140 characters. Zero and one
    are worded rather than templated, because a dead leaf parent and an
    unrendered placeholder must not read the same.
    """
    if subtree_orchestrations < 0:
        raise ValueError(f"subtree_orchestrations cannot be negative, got {subtree_orchestrations}")
    if subtree_orchestrations == 0:
        examined = f"no in-flight agent run under {run_code} and no nested orchestration beneath it"
    elif subtree_orchestrations == 1:
        examined = (
            f"no in-flight agent run under {run_code} or the 1 orchestration in its ticket subtree"
        )
    else:
        examined = (
            f"no in-flight agent run under {run_code} or the {subtree_orchestrations} "
            "orchestrations in its ticket subtree"
        )
    return (
        f"{ORCHESTRATION_LEASE_EXPIRED_PREFIX}: {examined}, and nothing has renewed it. "
        "Its driver is gone; the lane has been released."
    )
