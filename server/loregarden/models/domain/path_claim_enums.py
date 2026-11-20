"""Vocabulary for a ticket's claim on a file (`services.path_claims`).

A claim is derived, never stored: a ticket claims the files its plan names and
the files its runs have changed. The source says which, because a plan is a
prediction and a changed path is a fact, and the agent reading the warning
should weigh them differently.
"""

from __future__ import annotations

from enum import StrEnum


class PathClaimSource(StrEnum):
    """Where a ticket's claim on a path comes from."""

    #: Named in the newest plan artifact's `modules_touched`.
    PLANNED = "planned"
    #: In some run's recorded `changed_paths_json`.
    OBSERVED = "observed"
