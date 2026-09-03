"""Which channel carried a stage verdict, recorded rather than grepped.

Two channels say the same thing: the typed `loregarden_complete_stage` call, and
the `<<<LOREGARDEN_STAGE_REPORT>>>` sentinel parsed back out of stdout. Nothing
recorded which an agent used, so adherence could only be measured by searching
stdout — and doing that badly is how lg-workflow-integrity-95 came to cite 7.1%
adherence when the real figure was 56.5%.

The failure mode is specific and worth keeping in view: agents quote the contract
document, so the sentinel appears in prose, and a regex cannot tell a report from
a discussion of one. The production parser only survives that by taking the last
valid match.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import AgentRun, StageVerdictChannel
from loregarden.services.run_token_usage import adherence_by_agent, adherence_by_stage


def _run(agent: str, channel: StageVerdictChannel) -> AgentRun:
    return AgentRun(
        run_code=f"{agent}-{channel.value or 'none'}",
        ticket_id="t",
        workspace_id="w",
        agent_id=agent,
        stage_key="implement",
        verdict_channel=channel,
    )


def test_each_channel_is_counted_separately():
    rows = adherence_by_agent(
        [
            _run("planner", StageVerdictChannel.TOOL),
            _run("planner", StageVerdictChannel.SENTINEL),
            _run("planner", StageVerdictChannel.UNKNOWN),
        ]
    )

    assert len(rows) == 1
    row = rows[0]
    assert (row.tool, row.sentinel, row.unknown) == (1, 1, 1)
    assert row.reported == 2
    assert row.adherence == pytest.approx(2 / 3)


def test_unknown_is_not_folded_into_sentinel():
    """`unknown` holds runs that reported through neither channel and every row
    written before the column existed. Collapsing it into "sentinel" is exactly
    the error that produced the wrong adherence figures."""
    rows = adherence_by_agent([_run("triage", StageVerdictChannel.UNKNOWN)] * 3)

    row = rows[0]
    assert row.sentinel == 0
    assert row.unknown == 3
    assert row.reported == 0
    assert row.adherence == 0.0


def test_a_group_with_no_runs_has_no_adherence():
    """None rather than 0.0 — a group with nothing in it has not failed to
    report, and 0% would read as total non-compliance."""
    from loregarden.services.run_token_usage import VerdictAdherence

    assert VerdictAdherence(key="nobody").adherence is None


def test_the_least_adherent_agent_comes_first():
    """The useful question is which agents are not reporting."""
    rows = adherence_by_agent(
        [
            _run("good", StageVerdictChannel.TOOL),
            _run("bad", StageVerdictChannel.UNKNOWN),
            _run("bad", StageVerdictChannel.UNKNOWN),
        ]
    )
    assert [r.key for r in rows] == ["bad", "good"]
    assert rows[0].adherence == 0.0
    assert rows[1].adherence == 1.0


def test_grouping_by_stage_answers_a_different_question():
    runs = [
        _run("a", StageVerdictChannel.TOOL),
        _run("b", StageVerdictChannel.SENTINEL),
    ]
    runs[1].stage_key = "review"

    rows = adherence_by_stage(runs)
    assert {r.key for r in rows} == {"implement", "review"}


def test_the_tool_channel_wins_when_an_agent_uses_both():
    """An agent that calls the tool and also prints the sentinel used the typed
    channel; recording the fallback would overstate the sentinel's share. The
    guard lives in run_completion — this pins the intent."""
    run = _run("planner", StageVerdictChannel.TOOL)
    # run_completion only writes SENTINEL when the channel is still UNKNOWN.
    assert run.verdict_channel is StageVerdictChannel.TOOL


def test_rows_written_before_the_column_read_as_unknown():
    run = AgentRun(run_code="old", ticket_id="t", workspace_id="w", stage_key="implement")
    assert run.verdict_channel is StageVerdictChannel.UNKNOWN
