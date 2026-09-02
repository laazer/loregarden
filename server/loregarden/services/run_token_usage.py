"""What a ticket, or one of its stages, actually cost in tokens.

The whole point of this module is the difference between *zero* and *nobody
measured it*. Every run that finished before the usage columns existed has no
figures and never will; a local adapter reports none; a run killed mid-flight
may never print its usage event. Summing those in as zero is how a cost report
quietly understates itself, and it is not recoverable after the fact — the
average has already been taken.

So the totals here are ``int | None``. A group where no run reported a figure
totals ``None``, not ``0``, and every group carries ``measured_runs`` beside
``unmeasured_runs`` so a reader can see how much of the answer is real. A
single run that genuinely spent nothing still totals ``0`` and still counts as
measured, which is precisely the case a nullable column keeps separable.

Mirrors ``run_duration_stats``, which refuses to invent a duration for the same
reason.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from loregarden.models.domain import AgentRun
from sqlmodel import Session, col, select


@dataclass(frozen=True)
class TokenTotals:
    """Summed usage for one group of runs, and how complete that sum is.

    ``input_tokens``, ``output_tokens``, ``cache_read_tokens`` and
    ``cache_write_tokens`` are ``None`` when no run in the group reported that
    column at all. Read them beside ``unmeasured_runs``: totals over a group
    that is mostly unmeasured are a floor, not a cost.
    """

    key: str
    runs: int = 0
    measured_runs: int = 0
    unmeasured_runs: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        """Every token the group is known to have spent, or None if none is known.

        Input, cache reads and cache writes are disjoint by construction (see
        ``agents.run_usage``), so this is a true sum rather than a double count.
        """
        parts = [
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        ]
        known = [part for part in parts if part is not None]
        return sum(known) if known else None


def _add(total: int | None, value: int | None) -> int | None:
    """Fold one run's figure into a running total, leaving None untouched.

    An unreported figure contributes nothing *and does not make the total
    zero*: the total only becomes a number once some run has reported one.
    """
    if value is None:
        return total
    return value if total is None else total + value


def _measured(run: AgentRun) -> bool:
    return any(
        value is not None
        for value in (
            run.input_tokens,
            run.output_tokens,
            run.cache_read_tokens,
            run.cache_write_tokens,
        )
    )


def totals_for(runs: list[AgentRun], *, key: str = "") -> TokenTotals:
    """Sum one group of runs into a single `TokenTotals`."""
    totals = TokenTotals(key=key)
    for run in runs:
        measured = _measured(run)
        totals = TokenTotals(
            key=key,
            runs=totals.runs + 1,
            measured_runs=totals.measured_runs + (1 if measured else 0),
            unmeasured_runs=totals.unmeasured_runs + (0 if measured else 1),
            input_tokens=_add(totals.input_tokens, run.input_tokens),
            output_tokens=_add(totals.output_tokens, run.output_tokens),
            cache_read_tokens=_add(totals.cache_read_tokens, run.cache_read_tokens),
            cache_write_tokens=_add(totals.cache_write_tokens, run.cache_write_tokens),
        )
    return totals


def ticket_runs(session: Session, ticket_id: str) -> list[AgentRun]:
    return list(session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket_id)).all())


def usage_by_stage(runs: list[AgentRun]) -> list[TokenTotals]:
    """One `TokenTotals` per ``stage_key``, ordered by name.

    A stage that re-ran is one group, not several: rework is part of what the
    stage cost, and separating the attempts is what made the rework share
    unanswerable in the first place.
    """
    grouped: dict[str, list[AgentRun]] = defaultdict(list)
    for run in runs:
        grouped[run.stage_key].append(run)
    return [totals_for(grouped[key], key=key) for key in sorted(grouped)]


def ticket_usage(session: Session, ticket_id: str) -> tuple[TokenTotals, list[TokenTotals]]:
    """This ticket's whole token cost, and the same broken down by stage."""
    runs = ticket_runs(session, ticket_id)
    return totals_for(runs, key=ticket_id), usage_by_stage(runs)


#: Values an adapter puts in the model field that are not a model. Cursor's
#: selector answers "Auto", which says a model was chosen and not which one — the
#: tokens behind it cannot be priced, so it counts as unattributed rather than as
#: a model named "Auto". Measured 2026-09-02: of the runs in this database that
#: carry usage, 9 name no model and 4 name "Auto", all from `cursor-agent`
#: (lg-workflow-integrity-496).
NON_MODEL_VALUES = frozenset({"", "auto", "default", "unknown"})


def is_costable_model(model: str | None) -> bool:
    """Whether this model string can be turned into a price.

    The original ticket put it plainly: a token figure with no model attached
    cannot be turned into a cost. "Auto" is that case wearing a value.
    """
    return (model or "").strip().lower() not in NON_MODEL_VALUES


def unattributed_runs(runs: list[AgentRun]) -> int:
    """How many measured runs carry tokens nobody can price.

    Reported beside a total rather than folded into it: work that cannot be
    attributed to a model is a finding about the adapter, not a rounding error.
    """
    return sum(1 for run in runs if _measured(run) and not is_costable_model(run.model))


def usage_by_model(runs: list[AgentRun]) -> list[TokenTotals]:
    """One `TokenTotals` per model, heaviest known spend first.

    Runs whose adapter named no usable model group under ``(unattributed)``
    rather than being dropped — a cost report missing its most expensive
    unattributable rows is the failure this is meant to surface.
    """
    grouped: dict[str, list[AgentRun]] = defaultdict(list)
    for run in runs:
        name = (run.model or "").strip()
        grouped[name if is_costable_model(name) else "(unattributed)"].append(run)
    rows = [totals_for(grouped[key], key=key) for key in grouped]
    return sorted(rows, key=lambda r: r.total_tokens or 0, reverse=True)


def subtree_runs(session: Session, ticket_ids: list[str]) -> list[AgentRun]:
    """Runs across a set of tickets — a feature and its children, a milestone.

    Takes ids rather than walking the tree, so the caller keeps ownership of what
    "the subtree" means; `hierarchy_service.collect_ticket_scope_ids` already
    answers that.
    """
    if not ticket_ids:
        return []
    return list(session.exec(select(AgentRun).where(col(AgentRun.ticket_id).in_(ticket_ids))).all())
