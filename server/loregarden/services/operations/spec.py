"""What an operation is: one thing the control plane does, declared once.

Every transport (MCP, the CLI, REST) reaches an operation through
`operation_call`, never its handler directly, so an operation behaves the same
on each and turning one transport off is a line here, with its reason, rather
than a route nobody remembers to delete.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlmodel import Session


class Transport(StrEnum):
    """How a caller reached the control plane."""

    MCP = "mcp"
    #: `loregarden-cli.sh mcp call`: MCP's tool names, run in-process.
    CLI = "cli"
    REST = "rest"


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"


class OperationId(StrEnum):
    #: What an agent reads before acting on a ticket: position, requirement,
    #: blockers, documents.
    TICKET_BRIEF = "ticket_brief"
    #: Where a ticket sits in its workflow, with its edges and active run.
    TICKET_STATE = "ticket_state"
    #: The ticket page's view: the summary plus artifacts, transitions, runtime
    #: and delivery.
    TICKET_DETAIL = "ticket_detail"
    #: An agent's ticket lookup: short rows to choose from.
    TICKET_SEARCH = "ticket_search"
    #: The board's ticket list: full summaries, filtered and optionally paged.
    TICKET_SUMMARIES = "ticket_summaries"
    #: Which documents describe a ticket, up its ancestors, without bodies.
    ARTIFACT_INDEX = "artifact_index"
    #: Every artifact on one ticket, newest first, for the Artifacts tab.
    ARTIFACT_FEED = "artifact_feed"


@dataclass(frozen=True)
class Operation:
    id: OperationId
    effect: Effect
    #: Validates the caller's arguments; every transport passes the same model.
    args: type[BaseModel]
    handler: Callable[[Session, Any], Any]
    #: Transports this operation is not offered on, each with the reason. An
    #: absent transport is offered, and must be bound (`test_operations.py`).
    unavailable_on: Mapping[Transport, str] = field(default_factory=dict)


class OperationUnavailable(Exception):  # noqa: N818 — reads as the condition, like StopIteration
    """The operation is not offered on the caller's transport."""

    def __init__(self, operation: OperationId, transport: Transport, reason: str) -> None:
        super().__init__(f"{operation} is not available over {transport}: {reason}")
        self.operation = operation
        self.transport = transport
        self.reason = reason


class ReadOperationWrote(RuntimeError):  # noqa: N818 — reads as the condition
    """A read operation tried to write. Raised where reads are strict (tests)."""
