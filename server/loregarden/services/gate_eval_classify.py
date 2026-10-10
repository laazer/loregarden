"""Classify one failed gate evaluation as harness, work, inherited or unknown.

Rules are ordered and the first match wins, harness before work: a message that
carries both a uv warning and a ruff finding is reported as harness. That is the
conservative direction for an eval — it can only make an agent look better than
the gate did, never worse for something it did not do — and the scorecard counts
episodes ever blocked by harness separately, so the choice stays visible.

Every rule reads the gate's own `message`, which is all a historical event has.
A new failure shape lands in `unknown`, which the scorecard lists by message so
the table below can grow from evidence rather than guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loregarden.db.migrations_stage_keys import CANONICAL_STAGE_RENAMES
from loregarden.models.domain import GateFailureCategory, GateFailureKind, GateOutcome


def canonical_stage(key: str) -> str:
    """One spelling per stage. Migration 0114 renamed stored stage keys, but not
    those inside event payloads, so older `GateEvaluated` rows still say
    `implementation` where newer ones say `implement`."""
    return CANONICAL_STAGE_RENAMES.get(key, key)


#: Stages whose agents write no code. A work-shaped failure at one of these is
#: about files the stage never touched — `ruff format` on triage→plan failing on
#: another ticket's leftovers is the recorded case. Canonical spellings only.
NON_CODE_STAGES = frozenset(
    {"triage", "plan", "plan-synthesis", "domain_consultation", "ui-design", "spec"}
)


@dataclass(frozen=True)
class _Rule:
    category: GateFailureCategory
    kind: GateFailureKind
    pattern: re.Pattern[str]


def _rule(category: GateFailureCategory, kind: GateFailureKind, pattern: str) -> _Rule:
    return _Rule(category, kind, re.compile(pattern, re.IGNORECASE | re.DOTALL))


_HARNESS_RULES = (
    _rule(GateFailureCategory.TIMEOUT, GateFailureKind.HARNESS, r"timed out after"),
    _rule(
        GateFailureCategory.BROKEN_WORKTREE,
        GateFailureKind.HARNESS,
        r"cannot determine what to examine|must be run in a work tree",
    ),
    # uv's warning never fails a command: uv exits with the child's code and the
    # child's findings go to stdout. `gate_runner` records `stderr or stdout`, so
    # the warning *replaces* whatever really failed. Harness, because the record
    # is the harness's defect and the real cause cannot be recovered from it.
    _rule(
        GateFailureCategory.VIRTUAL_ENV_MISMATCH,
        GateFailureKind.HARNESS,
        r"VIRTUAL_ENV=.*does not match",
    ),
    # `task` with an unknown or missing task name prints its task list and exits
    # non-zero; the gate then reports the listing as lint_diff_check's output.
    _rule(
        GateFailureCategory.TASK_HELP_LISTING,
        GateFailureKind.HARNESS,
        r"lint_diff_check.*(task: Available tasks for this project|Usage:\s*task \[)",
    ),
)

#: Most specific first: lint_diff_check wraps other tools' output, so its own
#: marker is the fallback, not the first thing tried.
_WORK_RULES = (
    _rule(GateFailureCategory.FORMAT, GateFailureKind.WORK, r"Would reformat|\bI001\b|un-sorted"),
    _rule(
        GateFailureCategory.PY_ORGANIZATION,
        GateFailureKind.WORK,
        r"Python organization check failed",
    ),
    _rule(
        GateFailureCategory.TS_ORGANIZATION,
        GateFailureKind.WORK,
        r"TypeScript organization check failed",
    ),
    _rule(GateFailureCategory.TODO_VALIDATION, GateFailureKind.WORK, r"todo_validation_check FAIL"),
    _rule(GateFailureCategory.LINT_DIFF, GateFailureKind.WORK, r"lint_diff_check FAIL"),
)


@dataclass(frozen=True)
class GateFailureClass:
    kind: GateFailureKind
    category: GateFailureCategory


def classify_gate_failure(
    message: str, *, from_stage: str, outcome: GateOutcome = GateOutcome.FAILED
) -> GateFailureClass:
    """Kind and category of one failed evaluation. *from_stage* must be canonical."""
    for rule in _HARNESS_RULES:
        if rule.pattern.search(message):
            return GateFailureClass(rule.kind, rule.category)
    if outcome is GateOutcome.UNAVAILABLE:
        # The runner already decided the gate could not run; no agent is at fault.
        return GateFailureClass(GateFailureKind.HARNESS, GateFailureCategory.UNAVAILABLE)
    for rule in _WORK_RULES:
        if rule.pattern.search(message):
            # A todo list is the stage's own output even at a planning stage.
            inherited = (
                from_stage in NON_CODE_STAGES
                and rule.category is not GateFailureCategory.TODO_VALIDATION
            )
            kind = GateFailureKind.INHERITED if inherited else rule.kind
            return GateFailureClass(kind, rule.category)
    return GateFailureClass(GateFailureKind.UNKNOWN, GateFailureCategory.UNCLASSIFIED)
