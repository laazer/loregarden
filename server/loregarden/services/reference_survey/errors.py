"""The survey's refusals. Each is a ValueError, so the MCP and REST layers already
turn it into a message the caller reads rather than a 500."""

from __future__ import annotations


class SurveyError(ValueError):
    """A survey operation refused, with the reason in words."""


class SurveyNotFound(SurveyError):
    """A source, pass, finding, evidence record or unknown that does not exist."""


class StaleRevision(SurveyError):
    """An unknown changed since the caller read it. Reread; do not retry blindly."""

    def __init__(self, unknown_id: str, expected: int, current: int) -> None:
        super().__init__(
            f"Unknown {unknown_id} is at revision {current}, not {expected}: someone updated it "
            "since you read it. Read it again and decide against what is there now."
        )
        self.current = current


class TicketWriteMismatch(SurveyError):
    """A ticket write returned success but the read-back differs from what was sent."""
