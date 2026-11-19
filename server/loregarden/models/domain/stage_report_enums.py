"""Vocabulary of the stage-report contract a run is given."""

from __future__ import annotations

from enum import Enum

__all__ = ["ContractAudience"]


class ContractAudience(str, Enum):
    """Which stage-report rules a run is given: a judging stage's, or everyone else's.

    A judging stage (`finding_bar.is_judging`: the verifier, the gatekeepers, every
    review lens) rejects work and must back a reject with findings; nobody else
    writes `findings` or `rechecks`. Handing those rules to an implementer cost
    every implement prompt about a third of its stage-report contract.
    """

    JUDGING = "judging"
    OTHER = "other"
