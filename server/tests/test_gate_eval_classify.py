"""Failure kinds for the gate eval: who a failed gate is evidence against.

The classifier decides which failures an agent is scored on, so a wrong rule is
not cosmetic — a harness failure read as work makes a model look worse for the
machine it ran on. Each rule is pinned by a message shaped like the ones the
gates emit, and the ordering rules (harness first, inherited by stage) by the
collisions they exist to settle.
"""

import pytest
from loregarden.models.domain import GateFailureCategory, GateFailureKind, GateOutcome
from loregarden.services.gate_eval_classify import canonical_stage, classify_gate_failure

C = GateFailureCategory
K = GateFailureKind


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Gate command timed out after 300s", C.TIMEOUT),
        ("fatal: this operation must be run in a work tree", C.BROKEN_WORKTREE),
        ("git diff: cannot determine what to examine", C.BROKEN_WORKTREE),
        (
            "warning: `VIRTUAL_ENV=/x/.venv` does not match the project environment path `.venv`",
            C.VIRTUAL_ENV_MISMATCH,
        ),
        (
            "lint_diff_check FAIL\ntask: Available tasks for this project:\n* dev: run",
            C.TASK_HELP_LISTING,
        ),
    ],
)
def test_harness_patterns_are_not_charged_to_the_agent(message, category):
    got = classify_gate_failure(message, from_stage="implement")
    assert (got.kind, got.category) == (K.HARNESS, category)


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Would reformat: src/a.py", C.FORMAT),
        ("src/a.py:1:1: I001 Import block is un-sorted", C.FORMAT),
        ("Python organization check failed:\nsrc/a.py:3: isinstance", C.PY_ORGANIZATION),
        ("TypeScript organization check failed:\na.ts:2: instanceof", C.TS_ORGANIZATION),
        ("todo_validation_check FAIL: 2 open items", C.TODO_VALIDATION),
        ("lint_diff_check FAIL\nsrc/a.py:4: E501", C.LINT_DIFF),
    ],
)
def test_work_patterns_at_a_code_stage_are_work(message, category):
    got = classify_gate_failure(message, from_stage="implement")
    assert (got.kind, got.category) == (K.WORK, category)


def test_work_shaped_failure_at_a_stage_that_writes_no_code_is_inherited():
    got = classify_gate_failure("Would reformat: src/other.py", from_stage="triage")
    assert (got.kind, got.category) == (K.INHERITED, C.FORMAT)


def test_todo_validation_stays_work_at_a_planning_stage():
    # The todo list is the planner's own output, not somebody else's file.
    got = classify_gate_failure("todo_validation_check FAIL", from_stage="plan")
    assert got.kind is K.WORK


def test_harness_wins_when_a_message_carries_both():
    message = "warning: `VIRTUAL_ENV=/x` does not match\nPython organization check failed"
    assert classify_gate_failure(message, from_stage="implement").kind is K.HARNESS


def test_lint_diff_is_the_fallback_after_the_tool_it_wraps():
    got = classify_gate_failure(
        "lint_diff_check FAIL\nWould reformat: a.py", from_stage="implement"
    )
    assert got.category is C.FORMAT


def test_unavailable_outcome_is_harness_even_without_a_known_message():
    got = classify_gate_failure(
        "npx: not found", from_stage="implement", outcome=GateOutcome.UNAVAILABLE
    )
    assert (got.kind, got.category) == (K.HARNESS, C.UNAVAILABLE)


def test_an_unmatched_failure_is_unknown_not_work():
    got = classify_gate_failure("something new broke", from_stage="implement")
    assert (got.kind, got.category) == (K.UNKNOWN, C.UNCLASSIFIED)


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [("implementation", "implement"), ("test_design", "test-design"), ("test_break", "test-break")],
)
def test_legacy_stage_spellings_normalize(legacy, canonical):
    assert canonical_stage(legacy) == canonical
    assert canonical_stage(canonical) == canonical
