"""Resolve how much freedom an agent has to change existing interfaces and tests.

Agents were told, unconditionally, to "maintain backward compatibility ... unless
explicitly instructed" — and nothing could ever explicitly instruct otherwise, so the
escape hatch was unreachable. They defended consumers that do not exist and worked
around tests encoding the wrong behaviour instead of fixing the design.

The posture makes that obligation a decision rather than a default, resolved per work
item:

    ticket's own value -> nearest ancestor that sets one -> workspace default

Milestones are tickets (``work_item_type=milestone``), so walking the parent chain
gives milestone-level control without a separate column or table.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import (
    COMPATIBILITY_POSTURE_CONTRACT,
    DEFAULT_COMPATIBILITY_POSTURE,
    CompatibilityPosture,
    Ticket,
    Workspace,
)
from sqlmodel import Session

# A ticket cannot legitimately nest this deep (milestone > feature > capability > task),
# so anything beyond it is a cycle introduced by bad data — bail rather than hang.
_MAX_ANCESTRY_DEPTH = 16


@dataclass(frozen=True)
class ResolvedPosture:
    posture: CompatibilityPosture
    #: Human-readable origin, e.g. "milestone 86-workflow-integrity". Shown to the agent
    #: and the operator so an inherited value is auditable rather than mysterious.
    source: str

    @property
    def contract(self) -> str:
        return COMPATIBILITY_POSTURE_CONTRACT[self.posture]


def coerce_posture(raw: str | None) -> CompatibilityPosture | None:
    """Parse a stored value, tolerating blank (inherit) and unknown (treat as unset).

    Unknown values must not hard-fail an agent run — they degrade to inheritance, which
    still yields a valid posture from an ancestor or the workspace default.
    """
    if not raw:
        return None
    try:
        return CompatibilityPosture(raw.strip().lower())
    except ValueError:
        return None


def apply_compatibility_posture(ticket: Ticket, raw: str) -> None:
    """Set a ticket's posture override from operator input ("" clears it).

    Validates rather than coercing: a silently-ignored typo would leave the operator
    believing they had licensed a change the agent never sees.
    """
    value = (raw or "").strip().lower()
    if value and coerce_posture(value) is None:
        valid = ", ".join(item.value for item in CompatibilityPosture)
        raise ValueError(f"Unknown compatibility posture '{value}'. Valid values: {valid}")
    ticket.compatibility_posture = value


def resolve_compatibility_posture(
    session: Session,
    ticket: Ticket | None,
    workspace: Workspace | None = None,
) -> ResolvedPosture:
    """Resolve the posture for ``ticket``, walking ancestors then the workspace."""
    if ticket is not None:
        own = coerce_posture(ticket.compatibility_posture)
        if own:
            return ResolvedPosture(own, f"this {ticket.work_item_type.value}")

        current = ticket
        for _ in range(_MAX_ANCESTRY_DEPTH):
            if not current.parent_ticket_id:
                break
            parent = session.get(Ticket, current.parent_ticket_id)
            if parent is None:
                break
            inherited = coerce_posture(parent.compatibility_posture)
            if inherited:
                return ResolvedPosture(
                    inherited,
                    f"inherited from {parent.work_item_type.value} {parent.external_id}",
                )
            current = parent

    if workspace is None and ticket is not None:
        workspace = session.get(Workspace, ticket.workspace_id)

    if workspace is not None:
        from_workspace = coerce_posture(workspace.compatibility_posture)
        if from_workspace:
            return ResolvedPosture(from_workspace, f"workspace default ({workspace.slug})")

    return ResolvedPosture(DEFAULT_COMPATIBILITY_POSTURE, "built-in default")
