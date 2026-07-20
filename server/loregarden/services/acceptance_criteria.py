"""Acceptance criteria storage — the one place a ``list[str]`` becomes stored JSON.

Ticket creation and operator edits both route through here. They used to strip and
serialize independently, which is how PATCH ended up unable to write criteria at
all: with no shared seam, the update path simply never grew one.

Kept free of service imports so ``orchestration`` and ``ticket_service`` can both
reach it — ``ticket_service`` already imports ``orchestration``, so anything either
of them shares has to sit below both.
"""

import json
from collections.abc import Iterable
from typing import Literal

#: How incoming criteria combine with what the ticket already stores.
CriteriaMode = Literal["replace", "append"]

CRITERIA_MODES: tuple[str, ...] = ("replace", "append")


def normalize_criteria(criteria: Iterable[str] | None) -> list[str]:
    """Strip each criterion and drop the ones that were only whitespace."""
    return [line.strip() for line in (criteria or []) if line.strip()]


def load_criteria(raw: str | None) -> list[str]:
    """Read a ticket's stored ``acceptance_criteria_json``.

    Tolerates the empty/NULL column that predates the field having a default.
    """
    if not raw:
        return []
    parsed = json.loads(raw)
    return normalize_criteria(parsed) if isinstance(parsed, list) else []


def serialize_criteria(criteria: Iterable[str] | None) -> str:
    """Normalize and encode criteria for ``Ticket.acceptance_criteria_json``."""
    return json.dumps(normalize_criteria(criteria))


def merge_criteria(
    existing: Iterable[str] | None,
    incoming: Iterable[str] | None,
    mode: CriteriaMode = "replace",
) -> list[str]:
    """Combine stored criteria with incoming ones under ``mode``.

    ``append`` skips criteria already present verbatim. Stages in this control
    plane re-run — a gate autofix retry is routine — and an append that ran twice
    would otherwise leave the ticket with each criterion listed twice.
    """
    incoming_norm = normalize_criteria(incoming)
    if mode == "replace":
        return incoming_norm

    merged = normalize_criteria(existing)
    seen = set(merged)
    for criterion in incoming_norm:
        if criterion not in seen:
            merged.append(criterion)
            seen.add(criterion)
    return merged
