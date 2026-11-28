"""The pure helpers every group decision is built from (`core/agent_groups`).

Spec lg-run-economics-2636 §2. These are what AC4 rests on: the combined gate
is `combined_gate_def` over the members, so a command dropped here is a gate
that silently stopped running for every group.
"""

from __future__ import annotations

import pytest
from loregarden.core.agent_groups import (
    combined_gate_def,
    group_span,
    group_timeout,
    mid_group_step_def,
    span_complete,
)
from loregarden.models.domain import StageStatus, WorkflowStageDef


def _stage(key: str, order: int, group: str = "g", **fields) -> WorkflowStageDef:
    return WorkflowStageDef(key=key, name=key, order=order, agent_group=group, **fields)


STAGES = [
    _stage("plan", 1, group=""),
    _stage("s3", 4),
    _stage("s1", 2),
    _stage("s2", 3),
    _stage("after", 5, group=""),
]


def test_a_span_is_every_member_of_the_steps_group_in_order():
    assert [s.key for s in group_span(STAGES, "s2")] == ["s1", "s2", "s3"]


@pytest.mark.parametrize("key", ["plan", "missing"])
def test_an_ungrouped_or_unknown_step_has_no_span(key):
    assert group_span(STAGES, key) == []


def test_the_combined_gate_is_the_ordered_deduplicated_union_keyed_by_the_last_member():
    first = _stage("A", 1, gate_commands=["ruff", "pytest"], required_evidence=["real_surface"])
    second = _stage("B", 2, gate_commands=["pytest", "tsc"], checklist=["x"])

    combined = combined_gate_def([first, second])

    assert combined.key == "B"
    assert combined.gate_commands == ["ruff", "pytest", "tsc"]
    assert combined.required_evidence == ["real_surface"]
    assert combined.checklist == ["x"]


def test_no_members_gate_command_is_missing_from_the_combined_gate():
    members = [
        _stage("m1", 1, gate_commands=["cmd-a"]),
        _stage("m2", 2, gate_commands=["cmd-b", "cmd-shared"]),
        _stage("m3", 3, gate_commands=["cmd-shared", "cmd-c"], stage_type="gate"),
    ]
    combined = combined_gate_def(members)
    for member in members:
        for command in member.gate_commands:
            assert combined.gate_commands.count(command) == 1


def test_a_combined_gate_over_nothing_is_refused():
    with pytest.raises(ValueError):
        combined_gate_def([])


def test_a_mid_group_step_is_judged_without_its_gates():
    stage = _stage(
        "s1", 1, gate_commands=["ruff"], required_evidence=["real_surface"], checklist=["x"]
    )
    blanked = mid_group_step_def(stage)
    assert (blanked.gate_commands, blanked.required_evidence, blanked.checklist) == ([], [], [])
    assert blanked.key == "s1" and stage.gate_commands == ["ruff"], "the original is untouched"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ({"s1": StageStatus.DONE, "s2": StageStatus.DONE, "s3": StageStatus.DONE}, True),
        ({"s1": StageStatus.DONE, "s2": StageStatus.WONT_DO, "s3": StageStatus.DONE}, True),
        ({"s1": StageStatus.DONE, "s2": StageStatus.DONE}, False),  # s3 missing = pending
        ({"s1": StageStatus.DONE, "s2": StageStatus.RUNNING, "s3": StageStatus.PENDING}, False),
        ({"s1": StageStatus.WONT_DO, "s2": StageStatus.WONT_DO, "s3": StageStatus.WONT_DO}, False),
    ],
    ids=["all-done", "skipped-member", "missing", "in-flight", "nothing-done"],
)
def test_a_span_is_complete_only_when_every_member_settled_and_one_did_work(statuses, expected):
    assert span_complete(group_span(STAGES, "s1"), statuses) is expected


def test_a_group_timeout_is_the_sum_and_unbounded_if_any_step_is():
    assert group_timeout([600, 1200]) == 1800
    assert group_timeout([600, None]) is None
    with pytest.raises(ValueError):
        group_timeout([])
