"""REST's way into an operation: `operation_call`, with refusals as HTTP statuses."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from fastapi import HTTPException
from loregarden.services.dependency_hold import DependencyHold
from loregarden.services.docker_leases import UnknownLease
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.initiative_autopilot import AutopilotRefusedError
from loregarden.services.initiative_membership import MembershipConflictError
from loregarden.services.memory_links import MemoryRelationError
from loregarden.services.memory_store import MemoryGraphNotConfiguredError
from loregarden.services.merge_queue import QueueNotFound, QueueRefusal
from loregarden.services.operations import (
    Caller,
    OperationId,
    OperationUnavailable,
    Transport,
    run_operation,
)
from loregarden.services.operations.disk_operations import (
    DiskNodeNotFound,
    DiskScanUnreadable,
    NoDiskScan,
)
from loregarden.services.operations.github_issue_operations import IssueNotLinked
from loregarden.services.operations.initiatives import PlanNotFound
from loregarden.services.organization_gate_service import UnknownWorkspaceError
from loregarden.services.pull_request_merge import MergeFailed, MergeRefused
from loregarden.services.run_resolution import RunNotFound
from loregarden.services.ticket_dependencies import DependencyCycleError
from loregarden.services.ticket_growth_guard import SizeCapError
from loregarden.services.ticket_ids import TicketNotFound
from loregarden.services.ticket_revision_guard import TicketRevisionConflict
from loregarden.services.work_admission import WorkQueued
from loregarden.services.workspace_integration import InstallerError
from pydantic import ValidationError
from sqlmodel import Session

_REST = Caller(transport=Transport.REST)


class _Status(NamedTuple):
    code: int
    #: Put before the exception's own words.
    prefix: str = ""
    #: The detail when the exception carries no words.
    when_empty: str = ""


#: Each refusal's status, found along the raised exception's MRO, so the most
#: specific class wins: a `TicketNotFound` is a `ValueError`, and a 404.
_STATUSES: Mapping[type[Exception], _Status] = {
    OperationUnavailable: _Status(404),
    TicketNotFound: _Status(404, when_empty="Ticket not found"),
    RunNotFound: _Status(404),
    PlanNotFound: _Status(404),
    # 409, as on the ticket routes: the same request with
    # ignore_prerequisites=true starts it.
    DependencyHold: _Status(409),
    DependencyCycleError: _Status(409),
    # The initiative already covers the ticket; the plan cannot run.
    MembershipConflictError: _Status(409),
    AutopilotRefusedError: _Status(409),
    # The merge queue: an entry or workspace that is not there, a move the
    # entry's state refuses (nothing changed), and a GitHub read that failed,
    # which is never answered as an empty result.
    QueueNotFound: _Status(404),
    QueueRefusal: _Status(409),
    GitHubReadError: _Status(502, prefix="Could not read from GitHub: "),
    # Machine, memory, GitHub issues and workspace integration: what is not
    # there, a bad edge, a state of this installation (not a bad request), and
    # a disk scan whose stored result cannot be read.
    UnknownLease: _Status(404),
    NoDiskScan: _Status(404),
    DiskNodeNotFound: _Status(404),
    IssueNotLinked: _Status(404),
    UnknownWorkspaceError: _Status(404),
    MemoryRelationError: _Status(422),
    MemoryGraphNotConfiguredError: _Status(409),
    InstallerError: _Status(409),
    DiskScanUnreadable: _Status(500),
    # A merge: nothing was attempted; GitHub was asked to merge and did not.
    MergeRefused: _Status(409),
    MergeFailed: _Status(502),
    ValueError: _Status(400),
}

#: How a route words one kind of refusal, where it always answered that kind
#: with its own status (a survey route's 422, a memory route's 409).
Refusal = Callable[[Exception], HTTPException]


def refusal(status: int) -> Refusal:
    """Answer the refusal with `status` and its message."""
    return lambda exc: HTTPException(status, str(exc))


#: Refusals whose detail is a dict the client reads fields back from (the
#: size cap's numbers, the current revision), on every route.
_WORDED: Mapping[type[Exception], Callable[[Any], HTTPException]] = {
    SizeCapError: lambda exc: HTTPException(400, detail=exc.payload),
    TicketRevisionConflict: lambda exc: HTTPException(409, detail=exc.payload),
}
_REFUSALS = (*_WORDED, *_STATUSES)


def _refused(exc: Exception, refusals: Mapping[type[Exception], Refusal]) -> HTTPException:
    for cls in type(exc).__mro__:
        if cls in refusals:
            return refusals[cls](exc)
        if cls in _WORDED:
            return _WORDED[cls](exc)
        if cls in _STATUSES:
            status = _STATUSES[cls]
            detail = f"{status.prefix}{exc}" if str(exc) else status.when_empty
            return HTTPException(status.code, detail)
    raise AssertionError(f"{type(exc).__name__} is not in _STATUSES")  # _REFUSALS caught it


def run_rest_operation(
    session: Session,
    operation_id: OperationId,
    arguments: Mapping[str, Any],
    *,
    refusals: Mapping[type[ValueError], Refusal] | None = None,
) -> Any:
    """Run an operation for a REST route.

    The handlers refuse the way the rest of the codebase does, with a
    `ValueError` carrying the reason; here that reads as a 400 rather than a
    500, a ticket or run that is not there as a 404, and work that waits for
    a lane (or on a prerequisite) as a 409, as is an edge that would close a
    dependency cycle (`_STATUSES`). `refusals` lets a route keep the status it
    always gave a refusal of its own kind; along the exception's MRO, the most
    specific class named by either wins.
    """
    try:
        return run_operation(session, operation_id, arguments, _REST)
    except WorkQueued as queued:
        raise HTTPException(409, detail=queued.as_dict()) from queued
    except ValidationError as exc:  # a ValueError, answered with its field errors
        raise HTTPException(422, exc.errors(include_url=False)) from exc
    except _REFUSALS as exc:
        raise _refused(exc, refusals or {}) from exc
