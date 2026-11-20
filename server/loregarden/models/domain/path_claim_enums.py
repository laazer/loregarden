"""Vocabulary for a ticket's claim on a file (`services.path_claims`).

A ticket claims the files its plan names, the files its runs have changed, and
the files an agent declared mid-run. The source says which, because a plan is a
prediction, a declaration is an intent and a changed path is a fact, and the
agent reading the warning should weigh them differently.
"""

from __future__ import annotations

from enum import StrEnum


class PathClaimSource(StrEnum):
    """Where a ticket's claim on a path comes from."""

    #: Named in the newest plan artifact's `modules_touched`.
    PLANNED = "planned"
    #: Declared by an agent mid-run with `loregarden_claim_paths`.
    DECLARED = "declared"
    #: In some run's recorded `changed_paths_json`.
    OBSERVED = "observed"


class StageParkReason(StrEnum):
    """Why a STAGE_PARK approval was raised, when its resolution depends on it.

    Stored in the approval's `tool_name`, which a park otherwise leaves empty.
    A park without a reason is the environment or boundary park, whose reject
    blocks in place.
    """

    #: An open sibling in the same landing tree claims files this ticket is
    #: about to write. Rejecting makes the ticket wait for the sibling.
    SIBLING_OVERLAP = "sibling_overlap"
