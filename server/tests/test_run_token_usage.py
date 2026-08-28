"""What a stage run cost, and the difference between zero and unmeasured.

The whole design turns on one distinction: a run that spent nothing and a run
nobody measured must not aggregate the same way. Everything else here — the
per-adapter parsers, the model and effort record, the harness's self-report —
exists so that distinction has real data behind it.
"""

import json
import os
from unittest.mock import patch

import pytest
from loregarden.agents.cli_adapters import CliInvocation, resolve_cli_invocation
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.run_usage import parse_run_usage
from loregarden.models.domain import AgentRun, CliAdapter
from loregarden.services.run_token_usage import ticket_usage, totals_for, usage_by_stage
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_workspace

WORKSPACE = "ws-usage"
TICKET = "t-usage"

# Captured off the live CLIs, trimmed to the fields this parser reads.
CLAUDE_RESULT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 8579,
            "cache_read_input_tokens": 21786,
            "output_tokens": 4,
        },
        "modelUsage": {
            "claude-haiku-4-5": {"outputTokens": 1, "canonicalModel": "claude-haiku-4-5"},
            "claude-opus-5[1m]": {"outputTokens": 4, "canonicalModel": "claude-opus-5"},
        },
    }
)
CURSOR_INIT = json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-4-8"})
CURSOR_RESULT = json.dumps(
    {
        "type": "result",
        "usage": {
            "inputTokens": 6553,
            "outputTokens": 22,
            "cacheReadTokens": 9984,
            "cacheWriteTokens": 0,
        },
    }
)
CODEX_RESULT = json.dumps(
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 32635,
            "cached_input_tokens": 24320,
            "cache_write_input_tokens": 0,
            "output_tokens": 23,
            "reasoning_output_tokens": 10,
        },
    }
)


def _opencode_step(input_tokens: int, output: int, reasoning: int) -> str:
    return json.dumps(
        {
            "type": "step_finish",
            "part": {
                "tokens": {
                    "total": input_tokens + output + reasoning,
                    "input": input_tokens,
                    "output": output,
                    "reasoning": reasoning,
                    "cache": {"write": 0, "read": 0},
                }
            },
        }
    )


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        make_workspace(session, workspace_id=WORKSPACE, slug=WORKSPACE)
        yield session


def _run(session: Session, run_code: str, stage_key: str = "implement", **usage) -> AgentRun:
    return make_agent_run(
        session,
        run_code=run_code,
        ticket_id=TICKET,
        workspace_id=WORKSPACE,
        stage_key=stage_key,
        **usage,
    )


# --- the distinction the whole ticket is about -------------------------------


def test_a_genuine_zero_and_an_unmeasured_run_aggregate_differently(session):
    """The one assertion that cannot be satisfied by a zero default.

    Both runs "have no tokens" if you read the column as a number. Only a
    nullable column can say that one of them was measured and the other was
    not, and only that difference keeps an unmeasured run out of a cost
    average instead of dragging it down.
    """
    measured_zero = totals_for([_run(session, "zero", input_tokens=0, output_tokens=0)], key="zero")
    unmeasured = totals_for([_run(session, "unknown")], key="unknown")

    assert measured_zero.input_tokens == 0
    assert measured_zero.total_tokens == 0
    assert measured_zero.measured_runs == 1
    assert measured_zero.unmeasured_runs == 0

    assert unmeasured.input_tokens is None
    assert unmeasured.total_tokens is None
    assert unmeasured.measured_runs == 0
    assert unmeasured.unmeasured_runs == 1


def test_an_unmeasured_run_does_not_dilute_the_average(session):
    """A stage with one 100k run and one unmeasured run costs 100k per
    *measured* run, not 50k per run. Averaging over ``runs`` is the mistake;
    ``measured_runs`` is the denominator that survives it."""
    totals = totals_for(
        [
            _run(session, "measured", input_tokens=90_000, output_tokens=10_000),
            _run(session, "unmeasured"),
        ],
        key="implement",
    )

    assert totals.runs == 2
    assert totals.measured_runs == 1
    assert totals.total_tokens == 100_000
    assert totals.total_tokens / totals.measured_runs == 100_000


def test_rows_written_before_the_columns_existed_do_not_break_an_aggregate(session):
    """Every one of the 911 rows already in the live database looks like this.

    They must neither raise nor contribute, and the measured run beside them
    must still report its own real figures.
    """
    for index in range(5):
        _run(session, f"legacy-{index}", stage_key="plan")
    _run(session, "new", stage_key="plan", input_tokens=1_000, output_tokens=250)

    total, by_stage = ticket_usage(session, TICKET)

    assert total.runs == 6
    assert total.unmeasured_runs == 5
    assert total.input_tokens == 1_000
    assert total.total_tokens == 1_250
    assert [stage.key for stage in by_stage] == ["plan"]


def test_cost_is_answerable_per_stage_with_reruns_folded_in(session):
    """Per stage_key, not per attempt: rework is part of what a stage cost, and
    splitting the attempts is what made the rework share unanswerable."""
    _run(session, "impl-1", stage_key="implement", input_tokens=1_000, output_tokens=100)
    _run(session, "impl-2", stage_key="implement", input_tokens=3_000, output_tokens=200)
    _run(session, "review-1", stage_key="review", input_tokens=500, output_tokens=50)

    runs = list(session.exec(select(AgentRun).where(AgentRun.ticket_id == TICKET)).all())
    by_stage = {stage.key: stage for stage in usage_by_stage(runs)}

    assert by_stage["implement"].runs == 2
    assert by_stage["implement"].input_tokens == 4_000
    assert by_stage["implement"].total_tokens == 4_300
    assert by_stage["review"].total_tokens == 550


def test_a_partially_reported_run_counts_as_measured_for_what_it_reported(session):
    """A harness that knows its output tokens but not its cache reads reports
    what it has. The unreported column stays None rather than becoming zero."""
    totals = totals_for([_run(session, "partial", output_tokens=42)], key="partial")

    assert totals.measured_runs == 1
    assert totals.output_tokens == 42
    assert totals.input_tokens is None
    assert totals.cache_read_tokens is None
    assert totals.total_tokens == 42


def test_the_usage_endpoint_keeps_nulls_as_nulls(client, db_session):
    """Serialized as JSON ``null``, not 0 — a client that saw 0 would put an
    unmeasured run into a cost figure as free work."""
    make_workspace(db_session, workspace_id=WORKSPACE, slug=WORKSPACE)
    make_agent_run(
        db_session,
        run_code="api-unmeasured",
        ticket_id=TICKET,
        workspace_id=WORKSPACE,
        stage_key="implement",
    )

    payload = client.get("/api/runs/usage", params={"ticket_id": TICKET}).json()

    assert payload["total"]["input_tokens"] is None
    assert payload["total"]["total_tokens"] is None
    assert payload["total"]["unmeasured_runs"] == 1
    assert payload["by_stage"][0]["key"] == "implement"


# --- what each adapter actually emits ----------------------------------------


def test_claude_usage_separates_cache_from_fresh_input_and_names_the_model():
    """Claude's ``input_tokens`` excludes both cache figures, so recording it
    alone would report a 30k-token turn as 2 tokens. The model is the one that
    produced the most output, not whichever key sorts first."""
    usage = parse_run_usage(CLAUDE_RESULT, adapter=CliAdapter.CLAUDE)

    assert usage.input_tokens == 2
    assert usage.output_tokens == 4
    assert usage.cache_read_tokens == 21786
    assert usage.cache_write_tokens == 8579
    assert usage.model == "claude-opus-5"


def test_cursor_usage_is_camel_case_and_the_model_comes_from_the_init_event():
    usage = parse_run_usage(f"{CURSOR_INIT}\n{CURSOR_RESULT}", adapter=CliAdapter.CURSOR)

    assert usage.input_tokens == 6553
    assert usage.output_tokens == 22
    assert usage.cache_read_tokens == 9984
    assert usage.cache_write_tokens == 0
    assert usage.model == "claude-opus-4-8"


def test_codex_input_tokens_have_the_cached_half_subtracted():
    """Codex is the odd one out: its ``input_tokens`` *includes*
    ``cached_input_tokens``. Storing it verbatim beside the cache column would
    count 24320 tokens twice and price the run at three times what it cost."""
    usage = parse_run_usage(CODEX_RESULT, adapter=CliAdapter.CODEX)

    assert usage.input_tokens == 32635 - 24320
    assert usage.cache_read_tokens == 24320
    assert usage.output_tokens == 23


def test_opencode_sums_every_step_and_bills_reasoning_as_output():
    """opencode prints no run total, only one ``step_finish`` per step. Reading
    the last event would report the last step as the whole run."""
    stream = "\n".join([_opencode_step(24_324, 3, 21), _opencode_step(1_000, 10, 5)])

    usage = parse_run_usage(stream, adapter=CliAdapter.OPENCODE)

    assert usage.input_tokens == 25_324
    assert usage.output_tokens == 39


def test_an_adapter_with_no_usage_surface_reports_nothing_rather_than_zero():
    """LM Studio and the local runner print no accounting at all. Unmeasured is
    the truthful answer; a zero would say the run was free."""
    usage = parse_run_usage(CLAUDE_RESULT, adapter=CliAdapter.LMSTUDIO)

    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert not usage.measured()


def test_a_stream_that_ended_before_its_usage_event_reports_nothing():
    """A killed run keeps its partial stdout. Nothing in it is a usage figure,
    and inventing one from the deltas would be a fabricated cost."""
    truncated = '{"type":"system","subtype":"init","session_id":"x"}\n{"type":"stream_ev'

    usage = parse_run_usage(truncated, adapter=CliAdapter.CLAUDE)

    assert not usage.measured()
    assert usage.model is None


# --- model and effort, as applied --------------------------------------------


def test_the_invocation_records_the_effort_that_was_resolved_not_the_one_configured(tmp_path):
    """Effort resolves per adapter (migration 0053): the claude pin and the
    cursor pin are different fields, and only the selected adapter's applies.
    Recording the configured value would attribute a cursor run's cost to a
    claude effort level that never reached the CLI."""
    # The suite forces every run onto the local adapter; this one is about the
    # claude branch, and bypass keeps it off the interactive bridge.
    overrides = {"LOREGARDEN_ALLOW_PERMISSION_BYPASS": "1", "LOREGARDEN_CLI_ADAPTER": "claude"}
    with patch.dict(os.environ, overrides, clear=False):
        invocation = resolve_cli_invocation(
            agent_id="backend_implementer",
            adapter="claude",
            prompt="x",
            prompt_file=tmp_path / "prompt.md",
            skill_name="",
            workspace_root=tmp_path,
            ticket_claude_model="claude-opus-5",
            ticket_claude_effort="xhigh",
            # The cursor pin must not leak into a claude run.
            ticket_cursor_effort="low",
        )

    assert invocation.model == "claude-opus-5"
    assert invocation.effort == "xhigh"
    assert "--effort" in invocation.argv
    assert invocation.argv[invocation.argv.index("--effort") + 1] == "xhigh"


def test_a_supervised_run_records_the_model_the_stream_reported_over_the_pin(session):
    """The pin says what was asked for; ``modelUsage`` says what answered. A
    cost query needs the second — an aliased or overridden pin would price the
    run against a model that never ran."""
    run = _run(session, "supervised")
    invocation = CliInvocation(argv=[], adapter="claude", model="opus", effort="high")

    CliAgentExecutor(session)._record_usage(run, stdout=CLAUDE_RESULT, invocation=invocation)
    session.refresh(run)

    assert run.model == "claude-opus-5"
    assert run.effort == "high"
    assert run.input_tokens == 2
    assert run.cache_read_tokens == 21786


def test_an_adapter_that_reports_no_usage_still_records_model_and_effort(session):
    """LM Studio prints no accounting, but what it ran under is known from the
    invocation. Tokens unmeasured, model and effort recorded."""
    run = _run(session, "lmstudio")
    invocation = CliInvocation(argv=[], adapter="lmstudio", model="qwen3-coder-30b", effort="high")

    CliAgentExecutor(session)._record_usage(run, stdout="hello", invocation=invocation)
    session.refresh(run)

    assert run.model == "qwen3-coder-30b"
    assert run.effort == "high"
    assert run.input_tokens is None
    assert run.output_tokens is None
