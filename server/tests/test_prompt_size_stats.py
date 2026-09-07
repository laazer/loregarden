"""Prompt size is recorded per stage, alongside how much of it was cached.

lg-workflow-integrity-498 proposed trimming stage-prompt boilerplate on the
grounds that ~53% of every prompt is invariant and re-sent 22 times per ticket.
The usage data inverts that: 95.6% of all input tokens across recorded runs are
cache READS, and the invariant boilerplate at the front of every prompt is what
makes the prefix stable enough to hit. Shortening the shared prefix can convert
cached tokens into fresh ones at roughly 10x the price.

So the measurement has to carry both numbers or it will be read the wrong way.
These tests pin that pairing, not a size target.
"""

from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
)
from loregarden.services.run_duration_stats import prompt_size_by_stage
from sqlmodel import Session
from tests.factories import make_workspace_ticket


def _run(
    db_session: Session,
    ticket: Ticket,
    *,
    stage_key: str,
    prompt_chars: int | None,
    fresh: int = 0,
    cached: int = 0,
) -> None:
    _run.n = getattr(_run, "n", 0) + 1
    db_session.add(
        AgentRun(
            run_code=f"psr_{_run.n}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            stage_key=stage_key,
            status=RunStatus.SUCCEEDED,
            prompt_chars=prompt_chars,
            input_tokens=fresh,
            cache_read_tokens=cached,
        )
    )
    db_session.commit()


def test_size_and_cache_share_are_reported_together(db_session: Session):
    ticket = make_workspace_ticket(db_session, "psr-pair")
    _run(db_session, ticket, stage_key="implement", prompt_chars=60_000, fresh=100, cached=9_900)

    stats = {s.stage_key: s for s in prompt_size_by_stage(db_session)}
    assert stats["implement"].median_chars == 60_000
    assert round(stats["implement"].cached_share, 3) == 0.99


def test_a_large_prompt_that_is_almost_entirely_cached_is_visible_as_such(
    db_session: Session,
):
    """The case the ticket's original framing would have misread: the biggest
    prompt is also the cheapest per token, because it is the one that hits."""
    ticket = make_workspace_ticket(db_session, "psr-cheap")
    _run(db_session, ticket, stage_key="implement", prompt_chars=60_000, fresh=10, cached=59_990)
    _run(db_session, ticket, stage_key="triage", prompt_chars=5_000, fresh=5_000, cached=0)

    stats = prompt_size_by_stage(db_session)
    assert [s.stage_key for s in stats] == ["implement", "triage"], "largest prompt first"
    biggest, smallest = stats
    assert biggest.cached_share > smallest.cached_share
    assert smallest.cached_share == 0.0


def test_runs_with_no_recorded_size_are_excluded_not_counted_as_zero(db_session: Session):
    """Null means nobody measured. Averaging it in as zero would understate
    every stage that predates the column — which is every stage today."""
    ticket = make_workspace_ticket(db_session, "psr-null")
    _run(db_session, ticket, stage_key="implement", prompt_chars=None)
    assert prompt_size_by_stage(db_session) == []

    _run(db_session, ticket, stage_key="implement", prompt_chars=40_000)
    stats = prompt_size_by_stage(db_session)
    assert len(stats) == 1
    assert stats[0].runs == 1, "the unmeasured run must not be averaged in"
    assert stats[0].median_chars == 40_000


def test_repeat_runs_at_one_stage_share_a_row(db_session: Session):
    """Two runs at a stage are one stage with two samples, not two stages.

    This used to assert that `implement` and `implementation` folded together.
    Migration 0114 renamed the forks away and CANONICAL_STAGE_KEYS is gone
    (lg-workflow-integrity-660 AC5), so the fold has nothing left to do — but the
    grouping it protected still needs pinning."""
    ticket = make_workspace_ticket(db_session, "psr-fork")
    _run(db_session, ticket, stage_key="implement", prompt_chars=10_000)
    _run(db_session, ticket, stage_key="implement", prompt_chars=20_000)

    stats = prompt_size_by_stage(db_session)
    assert len(stats) == 1
    assert stats[0].runs == 2


def test_cached_share_is_zero_when_nothing_is_known(db_session: Session):
    """No division by zero, and no invented confidence: a run with no token
    counts reports 0.0 rather than a share computed from nothing."""
    ticket = make_workspace_ticket(db_session, "psr-unknown")
    _run(db_session, ticket, stage_key="verify", prompt_chars=1_000)
    assert prompt_size_by_stage(db_session)[0].cached_share == 0.0


# --- the instrument is worthless if nothing writes to it --------------------


def test_a_dispatched_run_records_its_prompt_size(db_session: Session, monkeypatch, tmp_path):
    """AC3's load-bearing half, driven through the real dispatch path.

    A per-stage median over a column nothing sets reports an empty table forever
    and reads as "no prompts are large". Asserted end-to-end rather than by
    calling `_build_prompt`, because the recording deliberately does NOT live in
    that function: it is a pure function of its inputs, and mutating the run
    inside it marks the session dirty, which surfaced as "database is locked" and
    a lost telemetry row rather than as anything resembling its cause.
    """
    from loregarden.agents.executors import cli as cli_executor_module
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket = make_workspace_ticket(db_session, "psr-dispatch")
    run = AgentRun(
        run_code="psr_dispatch",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="static_qa",
        stage_key="testing",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    # Stop before the agent subprocess: the prompt has been built and recorded by
    # then, which is the whole of what this asserts.
    def refuse(**kwargs):
        raise RuntimeError("no subprocess in this test")

    monkeypatch.setattr(cli_executor_module, "resolve_cli_invocation", refuse)

    executor = CliAgentExecutor(db_session)
    try:
        executor.execute(run, ticket, skip_git_branch=True)
    except RuntimeError:
        pass

    db_session.refresh(run)
    assert run.prompt_chars is not None, "the dispatch path must record the size it sent"
    assert run.prompt_chars > 0
