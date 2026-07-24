"""Unit tests for the stage retry-budget module (ticket 105).

`stage_retry_budget` is the primitive behind the circuit breaker: a persisted,
per-(ticket, stage) dispatch counter, independent of whether those dispatches
passed or failed, and independent of how many member agents a parallel
dispatch involves.

Contract exercised here (see server/loregarden/services/stage_retry_budget.py):
- `record_stage_dispatch(session, ticket_id, stage_key) -> None` — called by
  the orchestrator exactly once per dispatch *pass*, before any agent for that
  pass runs (once for a whole parallel stage, not once per member). Persisted
  via a dedicated Artifact row, the same durable-counter pattern
  `_persisted_gate_fix_attempts` already uses in builtin_orchestrator.py —
  deliberately not derived from `AgentRun` row timestamps, since two
  dispatches of the same self-redoing stage within a single `execute()` call
  can land within the same instant (no real per-run time gap to cluster on),
  which is exactly the shape of the actual incidents this ticket exists to fix.
- `count_stage_dispatches(session, ticket_id, stage_key) -> int` — how many
  times `record_stage_dispatch` has been called for this (ticket, stage) pair,
  ever.
- `stage_retry_block_message(stage_key, attempts, max_attempts) -> str` — ""
  while `attempts < max_attempts`; a human-readable block message once
  `attempts >= max_attempts`, naming the stage and the numeric budget, worded
  to be accurate whether the exhausted attempts passed or failed.
- `exceeds_stage_retry_budget(session, ticket_id, stage_key, *, enabled,
  max_attempts) -> str` — the composed check the orchestrator calls before
  dispatch: "" when disabled or still within budget, else the block message.
"""

from loregarden.services.stage_retry_budget import (
    count_stage_dispatches,
    exceeds_stage_retry_budget,
    record_stage_dispatch,
    stage_retry_block_message,
)
from sqlmodel import Session

# -- record_stage_dispatch / count_stage_dispatches ---------------------------


def test_count_is_zero_for_a_stage_never_dispatched(db_session: Session):
    assert count_stage_dispatches(db_session, "t1", "implement") == 0


def test_a_single_dispatch_counts_as_one(db_session: Session):
    record_stage_dispatch(db_session, "t1", "implement")
    assert count_stage_dispatches(db_session, "t1", "implement") == 1


def test_four_dispatches_count_as_four(db_session: Session):
    for _ in range(4):
        record_stage_dispatch(db_session, "t1", "implement")
    assert count_stage_dispatches(db_session, "t1", "implement") == 4


def test_recording_back_to_back_with_no_time_gap_still_counts_each_one(db_session: Session):
    """The exact shape of a same-stage self-redo loop within one execute()
    call: no real time passes between dispatches at all. The counter must not
    rely on any wall-clock gap to tell dispatches apart."""
    for _ in range(5):
        record_stage_dispatch(db_session, "t1", "implement")
    assert count_stage_dispatches(db_session, "t1", "implement") == 5


def test_count_is_scoped_to_ticket_and_stage_together_not_globally(db_session: Session):
    for _ in range(3):
        record_stage_dispatch(db_session, "ticket-a", "implement")
    record_stage_dispatch(db_session, "ticket-b", "implement")
    for _ in range(2):
        record_stage_dispatch(db_session, "ticket-a", "review")

    assert count_stage_dispatches(db_session, "ticket-a", "implement") == 3
    assert count_stage_dispatches(db_session, "ticket-b", "implement") == 1
    assert count_stage_dispatches(db_session, "ticket-a", "review") == 2
    # A stage_key this ticket has never touched stays at zero even though the
    # ticket has other stages' dispatches recorded.
    assert count_stage_dispatches(db_session, "ticket-a", "gate") == 0


def test_count_is_not_reset_or_bypassed_by_a_fresh_session(db_session: Session, isolated_db):
    """AC1.4: the counter must hold across a simulated server restart —
    i.e. it is derived purely from persisted rows, not anything in-process."""
    for _ in range(5):
        record_stage_dispatch(db_session, "t1", "implement")
    assert count_stage_dispatches(db_session, "t1", "implement") == 5

    with Session(isolated_db) as fresh_session:
        assert count_stage_dispatches(fresh_session, "t1", "implement") == 5


def test_recording_one_stage_does_not_affect_another_stage_of_the_same_ticket(
    db_session: Session,
):
    """Requirement 1's parallel-vs-sequential correction lands at the call
    site (record once per pass regardless of member count) rather than in
    this module's counting logic, but the counting logic itself must still be
    exact and not leak between unrelated stage keys."""
    record_stage_dispatch(db_session, "t1", "script_review")
    record_stage_dispatch(db_session, "t1", "script_review")
    assert count_stage_dispatches(db_session, "t1", "script_review") == 2
    assert count_stage_dispatches(db_session, "t1", "implement") == 0


# -- stage_retry_block_message ------------------------------------------------


def test_block_message_empty_while_under_budget():
    assert stage_retry_block_message("static_qa", attempts=3, max_attempts=5) == ""
    assert stage_retry_block_message("static_qa", attempts=4, max_attempts=5) == ""


def test_block_message_present_once_budget_is_exhausted():
    """AC1.1 semantics: 5 prior dispatches already happened -> the 6th is blocked."""
    msg = stage_retry_block_message("static_qa", attempts=5, max_attempts=5)
    assert msg != ""


def test_block_message_names_the_stage_and_the_numeric_budget():
    """AC2.2: an operator reading it must see the stage key and the budget
    without digging into the Errors tab."""
    msg = stage_retry_block_message("static_qa", attempts=5, max_attempts=5)
    assert "static_qa" in msg
    assert "5" in msg


def test_block_message_wording_does_not_claim_repeated_failure():
    """AC3.2: the same message fires for a stage that kept *passing* without the
    workflow advancing past it, so it must not accuse it of repeated failure."""
    msg = stage_retry_block_message("script_review", attempts=5, max_attempts=5)
    lowered = msg.lower()
    assert "advanc" in lowered  # e.g. "...without the workflow advancing past it"
    assert "kept failing" not in lowered
    assert "repeated failure" not in lowered
    assert "after repeated failures" not in lowered


def test_block_message_scales_with_a_different_configured_budget():
    msg = stage_retry_block_message("implement", attempts=10, max_attempts=10)
    assert "10" in msg
    assert "implement" in msg


# -- exceeds_stage_retry_budget ------------------------------------------------


def test_exceeds_budget_returns_empty_when_disabled(db_session: Session):
    for _ in range(10):
        record_stage_dispatch(db_session, "t1", "implement")
    assert (
        exceeds_stage_retry_budget(db_session, "t1", "implement", enabled=False, max_attempts=5)
        == ""
    )


def test_exceeds_budget_allows_the_run_up_to_and_including_the_limit(db_session: Session):
    """AC1.2: the first `max_attempts` dispatches proceed with no change in
    behavior — the check only trips before what would be the (max_attempts+1)th."""
    for _ in range(4):
        record_stage_dispatch(db_session, "t1", "implement")
    assert (
        exceeds_stage_retry_budget(db_session, "t1", "implement", enabled=True, max_attempts=5)
        == ""
    )


def test_exceeds_budget_blocks_once_the_limit_is_reached(db_session: Session):
    """AC1.1: a 6th dispatch of a stage already dispatched 5 times is refused."""
    for _ in range(5):
        record_stage_dispatch(db_session, "t1", "implement")
    msg = exceeds_stage_retry_budget(db_session, "t1", "implement", enabled=True, max_attempts=5)
    assert msg != ""
    assert "implement" in msg


def test_exceeds_budget_is_keyed_on_ticket_and_stage_not_orchestration_run(db_session: Session):
    """The budget must accumulate across separate orchestration runs against
    the same ticket+stage — there is no orchestration_run_id in the signature
    at all, so a fresh run cannot reset it by construction."""
    for _ in range(5):
        record_stage_dispatch(db_session, "t1", "implement")
    # Simulating a brand new orchestration run against the same ticket: the
    # check still sees all 5 prior dispatches and still blocks.
    msg = exceeds_stage_retry_budget(db_session, "t1", "implement", enabled=True, max_attempts=5)
    assert msg != ""
