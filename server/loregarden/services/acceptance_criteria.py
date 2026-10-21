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

from pydantic import TypeAdapter

#: How incoming criteria combine with what the ticket already stores.
CriteriaMode = Literal["replace", "append"]

CRITERIA_MODES: tuple[str, ...] = ("replace", "append")

#: The most criteria a ticket may grow to. Past it, the work is a second ticket: a
#: list this long stops being checkable by one reviewer in one pass. Lists already
#: longer (78 tickets when the cap landed, 2026-10-21) stay editable, because the
#: guard refuses growth, not size.
MAX_ACCEPTANCE_CRITERIA = 12

CRITERIA_CAP_MESSAGE = (
    "A ticket holds at most {cap} acceptance criteria; this write would take it from "
    "{before} to {after}. Put the extra criteria on a new ticket under the same parent "
    "and relate the two (loregarden_create_ticket, then loregarden_link_relation), "
    "rather than adding a criterion or rewriting the ones there."
)

#: Only this module writes ``checked_criteria_json``, so anything else is a bug worth raising.
_CRITERIA_LIST = TypeAdapter(list[str])


def normalize_criteria(criteria: Iterable[str] | None) -> list[str]:
    """Strip each criterion and drop the ones that were only whitespace."""
    return [line.strip() for line in (criteria or []) if line.strip()]


class AcceptanceCriteriaCapError(ValueError):
    """A write would grow a ticket's criteria past :data:`MAX_ACCEPTANCE_CRITERIA`."""


def guard_criteria_growth(before: Iterable[str] | None, after: Iterable[str] | None) -> None:
    """Refuse a write that grows a criteria list past the cap.

    Every writer calls this with the list it found and the list it is about to
    store, so a list already over the cap may be edited or shortened, never grown.
    """
    before_count = len(normalize_criteria(before))
    after_count = len(normalize_criteria(after))
    if after_count > MAX_ACCEPTANCE_CRITERIA and after_count > before_count:
        raise AcceptanceCriteriaCapError(
            CRITERIA_CAP_MESSAGE.format(
                cap=MAX_ACCEPTANCE_CRITERIA, before=before_count, after=after_count
            )
        )


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


def load_checked(raw: str | None, criteria: Iterable[str]) -> list[str]:
    """Read ``checked_criteria_json``, keeping only criteria the ticket still has.

    Checks are stored by criterion text, not position, so reordering the list
    keeps them and rewording a criterion clears its check — it is a new claim.
    """
    if not raw:
        return []
    stored = set(normalize_criteria(_CRITERIA_LIST.validate_json(raw)))
    return [criterion for criterion in criteria if criterion in stored]


def serialize_checked(checked: Iterable[str] | None, criteria: Iterable[str]) -> str:
    """Encode the checked criteria, dropping any the ticket does not have."""
    wanted = set(normalize_criteria(checked))
    return json.dumps(list(dict.fromkeys(c for c in criteria if c in wanted)))


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
