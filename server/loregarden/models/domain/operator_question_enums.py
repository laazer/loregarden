"""Vocabulary for a question a workflow agent asks the operator (`services.operator_questions`).

The agent states how sure it is, how hard its default is to undo, and whether
it thinks the question should stop the stage. The server then applies a floor:
some questions block whatever the agent said, and each such reason is named.
"""

from __future__ import annotations

from enum import StrEnum

from loregarden.models.domain.enums import ApprovalKind


class QuestionConfidence(StrEnum):
    """How sure the asking agent is of its default."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class QuestionReversibility(StrEnum):
    """What it costs to undo the default if the operator overrides it."""

    #: A later stage re-runs and the work changes; nothing outside the branch moved.
    EASY = "easy"
    #: Undoing it means redoing a stage's work or rewriting tests already relied on.
    COSTLY = "costly"
    #: Something leaves the branch: data, an external system, a published artifact.
    IRREVERSIBLE = "irreversible"


class QuestionBlockReason(StrEnum):
    """Why a question stops the stage instead of carrying on with its default."""

    #: The agent said so.
    AGENT_ASKED = "agent_asked"
    #: The agent is not confident enough in its default to build on it.
    LOW_CONFIDENCE = "low_confidence"
    #: The default cannot be undone by re-running a stage.
    IRREVERSIBLE = "irreversible"
    #: The answer changes what an earlier stage settled: criteria, scope or tests.
    CHANGES_REQUIREMENTS = "changes_requirements"


class QuestionOutcome(StrEnum):
    """How an operator question was settled, as its answer records it."""

    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"


#: Pending rows of these kinds do not mean the next move is a person's: the
#: run carries on past them. Readers that treat any pending approval as "a
#: person must act" (lane release, `awaiting` writers) must leave them out.
ADVISORY_APPROVAL_KINDS: frozenset[ApprovalKind] = frozenset({ApprovalKind.OPERATOR_QUESTION})
