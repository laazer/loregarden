"""How long one agent dispatch may run: per-stage floors over the run's default.

Lifted out of `builtin_orchestrator` so the group dispatcher
(`agent_group_dispatch`) can size a group's budget from its steps' budgets
without importing the driver that imports it. `builtin_orchestrator`
re-exports both names.
"""

from __future__ import annotations

from loregarden.models.domain import WorkflowStageDef

#: Per-stage floors, calibrated 2026-09-11 against the 95th percentile of every
#: SUCCEEDED run's wall clock, per stage, with the sample size beside each. The
#: first version of this map (686) was sized by judgement; these are measured.
#:
#: Read the numbers with one caveat, which is why each floor sits *above* its
#: p95 rather than at it: the sample is right-censored. Every run in it completed
#: under the ceiling in force at the time — mostly 600s — so runs that needed
#: longer are absent by construction, and the true distribution has a heavier
#: tail than this one can show. A floor set exactly at a censored p95 would be
#: too small by an unknown amount. Rounding up to the next 300s step is the
#: margin for that, not padding.
#:
#: Raising a floor costs something real: `TIMEOUT_HARD_CAP_MULTIPLIER` is 4, so a
#: genuinely hung stage now burns four times the number below before it is
#: killed. That is the trade this map makes deliberately — a hang is rare and
#: visible, while a stage killed mid-work parks a ticket and looks like a defect
#: in the agent.
STAGE_TIMEOUT_BUDGETS: dict[str, int] = {
    # p95 2559s over 149 runs — the heaviest stage by a wide margin, and the one
    # whose old 600s ceiling produced most of the recorded timeout deaths.
    "implement": 2700,
    # No measured sample of their own (the split implementers are newer than the
    # runs here); they inherit `implement`'s shape, one scope narrower.
    "backend-impl": 1800,
    "frontend-impl": 1800,
    # p95 1968s over 15 runs. Small sample, wide spread — a consultation reads
    # far more than it writes.
    "domain_consultation": 2100,
    # p95 1811s over 8 runs. The smallest sample in this map; treat as
    # provisional and re-measure once it has run more.
    "branch-triage": 2100,
    "verify": 1800,  # p95 1633s over 46 runs
    "test-design": 1800,  # p95 1660s over 70 runs
    "gate": 1800,  # p95 1622s over 23 runs
    "test-break": 1500,  # p95 1343s over 75 runs
    "review": 1200,  # p95 1159s over 114 runs
    "plan": 1200,  # p95 997s over 113 runs
    "script_review": 900,  # p95 859s over 125 runs
    "learning": 900,  # p95 687s over 12 runs
    "spec": 900,  # p95 641s over 59 runs
    "triage": 900,  # p95 627s over 136 runs — p50 is 88s; the tail is the cost
}


def stage_timeout_seconds(stage_def: WorkflowStageDef, run_default: int | None) -> int | None:
    """This stage's agent budget: its own if it declares one, else a floor for
    the heavy stages, else the run's.

    One budget for every stage is why `implement` timed out at 600s while still
    producing output — 35% of `implement` runs that DID succeed ran longer than
    that, against 6% for `triage` (lg-workflow-integrity-686). The spread is
    structural: the stages differ in how much work they are, not in how lucky
    they got.

    Resolved at dispatch rather than written into templates by a migration.
    Every template in this installation that a run actually uses is
    operator-authored (`built_in=0`), so a migration wide enough to help them
    would rewrite operator data and bump a version pins refer to — which is what
    `test_skill_migration_preserves_existing_non_skill_data` exists to catch.
    A default costs no rows and covers templates authored after it lands.

    The budget resizes a bound, never introduces one: a run with no timeout
    keeps none, and a run whose own default is already larger keeps that.
    """
    if stage_def.timeout_seconds:
        return stage_def.timeout_seconds
    if run_default is None:
        return None
    return max(run_default, STAGE_TIMEOUT_BUDGETS.get(stage_def.key, 0))
