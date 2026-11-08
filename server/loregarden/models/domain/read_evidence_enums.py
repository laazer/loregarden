"""How much of a ticket's diff a review, verify or gate run read.

Its own module rather than a block in `enums.py`, which sits at the module size
cap; `models.domain.__init__` star-exports it like every sibling.
"""

from __future__ import annotations

from enum import StrEnum


class ReadEvidenceStatus(StrEnum):
    """What a run's own tool calls show it read of the diff (`services.read_evidence`).

    Recorded from the transcript, never from the run's report, so an agent
    cannot claim it.
    """

    #: Every changed file, or a patch of the whole diff.
    OK = "ok"
    #: Some of the changed files and not others. Shown, not refused: a lens that
    #: reads only the files in its remit is doing its job.
    INSUFFICIENT = "insufficient"
    #: Tool calls, and none of them read a changed file. A pass on this is refused.
    NONE = "none"
    #: A transcript with no tool calls at all: the run could not have read
    #: anything, which is the harness failing, not the review.
    NO_TOOLS = "no_tools"
