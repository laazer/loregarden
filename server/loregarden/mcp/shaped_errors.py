"""Recoverable conditions reach an agent as a reply, not a protocol error.

`mcp/protocol.py` has one `except Exception`, and every condition behind it —
a store nobody configured, a slug that does not resolve, a stale index — came
back as a JSON-RPC `isError`. An agent meeting `isError` has been told the
call was a mistake; it has not been told what to do instead.

Two properties hold this module together, and both are load-bearing.

**Classification is on exception TYPE.** This repo interpolates the caller's
own string into exception messages at sixteen sites, so a substring classifier
would let any caller forge any shaped reply simply by naming it — the same
hazard `services/stage_report.py` already has with its sentinel `findall` over
caller text.

**The default branch is a default, not a deny-list.** Anything unrecognised
keeps today's internal-error shape and keeps `isError`. A reassurance that
swallows a genuine `OperationalError` is worse than the raise it replaced.

`RecoverableToolCondition` subclasses `ValueError` deliberately: the sixteen
`raise ValueError("Workspace not found: …")` sites it replaces are caught by
`api/` and turned into HTTP 400. A base that stopped being a `ValueError`
would turn every one of those into a 500 from a change whose entire subject is
the MCP transport.
"""

from __future__ import annotations

from typing import Any


class RecoverableToolCondition(ValueError):
    """A condition an agent can do something about.

    `error`, `remedy` and `guidance` are FIELDS rather than one formatted
    sentence, because the envelope has to carry them apart: an agent acts on
    the remedy and changes its plan on the guidance.

    A later ticket adds a sixth condition by subclassing this; classification
    is by inheritance, so it is shaped without touching `shape`.
    """

    #: What went wrong, as a statement. The subject is appended.
    error = "A recoverable condition was met"
    #: What would make the call work. Addressed to whoever can act on it.
    remedy = ""
    #: What the agent should do now, with or without the remedy.
    guidance = ""

    def __init__(self, subject: str = "") -> None:
        self.subject = str(subject)
        self.error = self.describe(self.subject)
        super().__init__(self.error)

    def describe(self, subject: str) -> str:
        """The error line, and what `str(exc)` becomes.

        Overridable because the live raise sites came first: a condition that
        replaces an existing exception keeps the message its callers and tests
        already match, and gains the remedy and guidance beside it. Changing
        both at once would make a message regression look like this change
        working.
        """
        base = type(self).error
        return f"{base}: {subject}" if subject else base


class StoreUnconfigured(RecoverableToolCondition):
    """A memory or knowledge store that is absent, or present and unseeded."""

    error = "that store is not configured, or holds nothing yet"
    remedy = (
        "Point the store at a real path in settings, or write one record to it "
        "(loregarden_upsert_memory) so later reads have something to read."
    )
    guidance = (
        "Treat the empty answer as 'no store was read', not as 'nothing is known'. "
        "Carry on using what the ticket and the code already tell you."
    )


class UnknownWorkspaceSlug(RecoverableToolCondition):
    """A workspace slug that resolves to no `Workspace` row."""

    error = "no workspace has that slug"
    remedy = (
        "Call loregarden_list_tickets, or read the run context at the top of your "
        "prompt, for the workspace_slug this run actually belongs to."
    )
    guidance = (
        "Do not retry with the same slug. A typo here reads as 'nothing has been "
        "tried before', which is the most expensive wrong answer this tool can give."
    )


class UnknownTicketExternalId(RecoverableToolCondition):
    """A ticket external id that resolves to no row in the queried workspace."""

    error = "no ticket in this workspace has that external id"
    remedy = (
        "Resolve the id first with loregarden_list_tickets (search=), or pass the "
        "ticket UUID. A bare ticket number is not unique across workspaces."
    )
    guidance = (
        "The ticket may live in another workspace, or the id may be a legacy one. "
        "Do not create a replacement ticket on the assumption it is missing."
    )


class StaleIndex(RecoverableToolCondition):
    """An index that exists and is older than the tree it describes."""

    error = "the index behind this answer is older than the working tree"
    remedy = (
        "You can suggest the user run `task index` once. Indexing is the user's "
        "decision to make — suggest it once and do not perform it yourself."
    )
    guidance = (
        "Fall back to your own file tools — grep, glob, read — for the rest of this "
        "session rather than asking this tool again."
    )


class StaleServerBinary(RecoverableToolCondition):
    """A running server older than the code in the tree, so a fix is not loaded."""

    error = "the running server is older than the code in this tree"
    remedy = (
        "Reload the backend with `touch server/.self-improve-restart`; the dev "
        "server ignores .py changes otherwise."
    )
    guidance = (
        "Anything you just changed on the server side is not loaded yet, so a result "
        "that contradicts your edit is evidence about the binary, not about the edit."
    )


def shape(exc: BaseException) -> dict[str, Any]:
    """Classify `exc` for the tool-reply envelope.

    The return is always the same four keys, so the boundary needs no branch
    of its own beyond reading `shaped`. `shaped` false means unchanged: the
    agent still sees `str(exc)` and nothing invented.

    `__cause__` is deliberately not walked. A tool that caught a recoverable
    condition and re-raised something else chose to fail; inheriting a
    reassurance from a decision already made would be the widest version of
    this module's own failure class.
    """
    if issubclass(type(exc), RecoverableToolCondition):
        return {
            "shaped": True,
            "error": exc.error,
            "remedy": exc.remedy,
            "guidance": exc.guidance,
        }
    return {"shaped": False, "error": str(exc), "remedy": "", "guidance": ""}
