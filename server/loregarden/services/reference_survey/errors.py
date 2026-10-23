"""The survey's refusals. Each is a ValueError, so the MCP and REST layers already
turn it into a message the caller reads rather than a 500."""

from __future__ import annotations


class SurveyError(ValueError):
    """A survey operation refused, with the reason in words."""


class SurveyNotFound(SurveyError):
    """A source, pass, finding, evidence record or unknown that does not exist."""


class AlreadyServes(SurveyError):
    """The source is already in the record for every workspace asked for."""

    def __init__(self, message: str, source_id: str) -> None:
        super().__init__(message)
        self.source_id = source_id


#: Longest reason a refusal message quotes; the source's page carries the rest.
BRIEF_REASON_CHARS = 160


def brief_reason(reason: str) -> str:
    """A refusal reason cut to its first clause, for a message that quotes it.

    `refused_reason` can be a whole survey-ledger note (legacy imports keep up to
    2000 characters of one), and a message that quotes all of it buries the one
    thing it has to say. The first clause names the cause (the licence, the
    measurement); the source's page shows the reason in full.
    """
    text = " ".join(reason.split())
    if not text:
        return "no reason recorded"
    for stop in (" — ", ". ", "; "):
        head, found, _ = text.partition(stop)
        if found and head:
            text = head
    if len(text) > BRIEF_REASON_CHARS:
        text = text[: BRIEF_REASON_CHARS - 1].rstrip() + "…"
    return text


class AlreadyRefused(SurveyError):
    """The source is in the record and was refused, so adding it again reads nothing."""

    def __init__(self, url: str, reason: str, source_id: str) -> None:
        super().__init__(
            f"{url} is already in the library, refused: {brief_reason(reason)}. Open it to "
            "read the full reason, or to review it again."
        )
        self.source_id = source_id


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
