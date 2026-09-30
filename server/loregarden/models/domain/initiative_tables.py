"""Global initiative number pool — one row, one counter.

Initiatives are not workspace-bound, so they cannot draw from
``workspaces.last_ticket_number``. The pool mirrors ``docker_capacity_pool``:
a singleton keyed by ``id='global'`` that ``next_initiative_number`` can
UPDATE without racing a per-workspace column.
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel

#: There is one initiative counter for the whole control plane.
GLOBAL_INITIATIVE_POOL_ID = "global"


class InitiativeNumberPool(SQLModel, table=True):
    """High-water mark for initiative ``ticket_number`` values. Exactly one row."""

    __tablename__ = "initiative_number_pool"

    id: str = Field(default=GLOBAL_INITIATIVE_POOL_ID, primary_key=True)
    last_initiative_number: int = Field(default=0)
