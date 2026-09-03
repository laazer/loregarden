"""Run-duration medians, and the queue-clear projection built on them.

These replaced a hardcoded 300 seconds per run. The tests that matter are the
ones pinning what happens when there is nothing to learn from, and that the
projection schedules rather than sums.
"""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import RunStatus
from loregarden.services.run_duration_stats import (
    CANONICAL_STAGE_KEYS,
    FALLBACK_KEY,
    canonical_stage_key,
    estimate_for,
    load_duration_stats,
    median_duration_by_agent,
    project_clear_time,
)
from sqlmodel import Session
from tests.factories import (
    make_agent_run,
    make_orchestration_run,
    make_ticket,
    make_workspace,
)

WORKSPACE = "ws-1"


def _finished_run(session: Session, agent_id: str, seconds: float, status=RunStatus.SUCCEEDED):
    started = datetime.now(timezone.utc) - timedelta(days=1)
    # The run's workspace and ticket have to exist; these tests are about the
    # duration medians, so the rows themselves carry nothing.
    make_workspace(session, workspace_id=WORKSPACE, slug=WORKSPACE)
    return make_agent_run(
        session,
        run_code=f"r-{agent_id}-{seconds}-{status.value}",
        ticket_id="t-1",
        workspace_id=WORKSPACE,
        agent_id=agent_id,
        status=status,
        started_at=started,
        finished_at=started + timedelta(seconds=seconds),
    )


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def test_no_history_yields_no_medians(session):
    """The empty dict is the signal callers key off to say "unknown"; a zero
    here would be read as "instant"."""
    assert median_duration_by_agent(session, WORKSPACE) == {}


def test_median_is_per_agent_with_a_workspace_fallback(session):
    _finished_run(session, "backend_implementer", 100)
    _finished_run(session, "backend_implementer", 200)
    _finished_run(session, "backend_implementer", 300)
    _finished_run(session, "test_designer", 20)

    medians = median_duration_by_agent(session, WORKSPACE)

    assert medians["backend_implementer"] == 200
    assert medians["test_designer"] == 20
    assert medians[FALLBACK_KEY] == 150  # median of 20, 100, 200, 300


def test_failed_runs_do_not_count(session):
    """A run that died in ten seconds is a real duration and a useless
    prediction — including it makes the estimate optimistic on exactly the
    agents that fail most."""
    _finished_run(session, "flaky", 400)
    _finished_run(session, "flaky", 10, status=RunStatus.FAILED)

    assert median_duration_by_agent(session, WORKSPACE)["flaky"] == 400


def test_another_workspace_is_not_borrowed_from(session):
    _finished_run(session, "backend_implementer", 100)

    assert median_duration_by_agent(session, "ws-other") == {}


def test_estimate_falls_back_for_an_unseen_agent(session):
    medians = {"known": 50.0, FALLBACK_KEY: 50.0}

    assert estimate_for(medians, "known") == 50.0
    assert estimate_for(medians, "never_run_here") == 50.0
    assert estimate_for({}, "known") is None


def test_clear_time_is_none_without_history():
    assert project_clear_time([], [{"agent_id": "a"}], {}, 3) is None


def test_an_empty_queue_clears_immediately():
    assert project_clear_time([], [], {FALLBACK_KEY: 100.0}, 3) == 0.0


def test_queued_runs_fill_slots_in_parallel():
    """Three runs across three slots is one run's wait, not three. Summing —
    what the old 300-per-run arithmetic did — gets this wrong by the slot
    count."""
    medians = {FALLBACK_KEY: 100.0}
    runs = [{"agent_id": "a"} for _ in range(3)]

    assert project_clear_time([], runs, medians, 3) == 100.0


def test_a_queue_deeper_than_the_slots_takes_another_pass():
    medians = {FALLBACK_KEY: 100.0}
    runs = [{"agent_id": "a"} for _ in range(5)]

    # Five runs over three slots: two slots take a second run, so two passes.
    assert project_clear_time([], runs, medians, 3) == 200.0


def test_active_runs_are_counted_by_time_remaining():
    medians = {FALLBACK_KEY: 100.0}
    active = [{"agent_id": "a", "elapsed_seconds": 60}]

    # 40s left on the occupied slot, and the queued run starts on a free one.
    assert project_clear_time(active, [{"agent_id": "a"}], medians, 3) == 100.0


def test_an_overdue_run_is_expected_imminently_not_negatively():
    medians = {FALLBACK_KEY: 100.0}
    active = [{"agent_id": "a", "elapsed_seconds": 500}]

    assert project_clear_time(active, [], medians, 3) == 0.0


def test_slower_agents_push_the_projection_out():
    """The whole reason for per-agent medians: a queue of slow work must not
    read the same as a queue of fast work."""
    medians = {"slow": 600.0, "fast": 10.0, FALLBACK_KEY: 300.0}

    slow = project_clear_time([], [{"agent_id": "slow"}] * 3, medians, 3)
    fast = project_clear_time([], [{"agent_id": "fast"}] * 3, medians, 3)

    assert slow == 600.0
    assert fast == 10.0


# --- forked stage keys --------------------------------------------------------
#
# Stage keys are spelled differently across templates: implement/implementation,
# test_design/test-design, plan/planning, spec/specification. Aggregation grouped
# by the raw key and dropped anything under MIN_SAMPLES, so the smaller variant
# of each pair got no median at all and the larger one was computed from a
# fraction of the real sample. Every consumer inherited it — queue_status,
# ticket_tree_estimate, parallel_queue, and the workflow monitor whose thresholds
# are meant to be relative to these baselines (lg-workflow-integrity-558).


def _staged_run(session: Session, stage_key: str, seconds: float, *, code: str):
    started = datetime.now(timezone.utc) - timedelta(days=1)
    make_workspace(session, workspace_id=WORKSPACE, slug=WORKSPACE)
    return make_agent_run(
        session,
        run_code=code,
        ticket_id="t-1",
        workspace_id=WORKSPACE,
        agent_id="impl",
        stage_key=stage_key,
        status=RunStatus.SUCCEEDED,
        started_at=started,
        finished_at=started + timedelta(seconds=seconds),
    )


def test_two_forked_spellings_now_reach_one_median(session):
    """AC4, and the exact failure this ticket describes.

    Two observations each: under raw grouping neither spelling reaches
    MIN_SAMPLES = 3, so the stage had no median at all despite four real runs.
    """
    for i, seconds in enumerate((100, 200)):
        _staged_run(session, "implement", seconds, code=f"a{i}")
    for i, seconds in enumerate((300, 400)):
        _staged_run(session, "implementation", seconds, code=f"b{i}")

    stats = load_duration_stats(session, WORKSPACE)

    assert stats.by_stage["implement"] == 250  # median of 100/200/300/400
    # The raw variant is not a separate entry — that was the bug.
    assert "implementation" not in stats.by_stage


def test_either_spelling_finds_the_shared_median(session):
    """A caller holding the raw key from a ticket or template must not miss the
    median stored under its canonical twin."""
    for i, seconds in enumerate((100, 200, 300)):
        _staged_run(session, "implementation", seconds, code=f"c{i}")

    stats = load_duration_stats(session, WORKSPACE)

    assert stats.stage_seconds("implementation", "impl") == 200
    assert stats.stage_seconds("implement", "impl") == 200


def test_an_unforked_stage_is_unchanged(session):
    """Unknown keys pass through. Inventing a normalisation rule for them would
    silently merge stages that only look similar."""
    for i, seconds in enumerate((10, 20, 30)):
        _staged_run(session, "review", seconds, code=f"d{i}")

    stats = load_duration_stats(session, WORKSPACE)
    assert stats.by_stage["review"] == 20


def test_the_mapping_is_one_named_constant(session):
    """AC2. A mapping that lives in three places is a mapping that disagrees
    with itself."""
    assert canonical_stage_key("implementation") == "implement"
    assert canonical_stage_key("test_design") == "test-design"
    assert canonical_stage_key("planning") == "plan"
    assert canonical_stage_key("specification") == "spec"
    # Canonical spellings are fixed points, so applying it twice is safe.
    for raw, canonical in CANONICAL_STAGE_KEYS.items():
        assert canonical_stage_key(canonical) == canonical, f"{raw} -> {canonical} not stable"


def test_rerun_rate_counts_a_forked_stage_once(session):
    """`attempts_per_stage` divides attempts by distinct (orchestration, stage)
    pairs. Two spellings of one stage would count as two pairs and deflate the
    re-run rate the monitor's thresholds are relative to."""
    started = datetime.now(timezone.utc) - timedelta(days=1)
    make_workspace(session, workspace_id=WORKSPACE, slug=WORKSPACE)
    # A real parent row: foreign keys are enforced on every engine here, so a
    # bare id would fail the insert rather than the assertion.
    make_ticket(session, ticket_id="t-1", workspace_id=WORKSPACE)
    make_orchestration_run(
        session, workspace_id=WORKSPACE, ticket_id="t-1", orchestration_run_id="orch-1"
    )
    for i, key in enumerate(("implement", "implementation")):
        make_agent_run(
            session,
            run_code=f"e{i}",
            ticket_id="t-1",
            workspace_id=WORKSPACE,
            orchestration_run_id="orch-1",
            agent_id="impl",
            stage_key=key,
            status=RunStatus.SUCCEEDED,
            started_at=started,
            finished_at=started + timedelta(seconds=60),
        )

    stats = load_duration_stats(session, WORKSPACE)
    # Two attempts at one stage, not one attempt at each of two stages.
    assert stats.attempts_per_stage == 2.0
