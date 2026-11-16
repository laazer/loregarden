"""REST's way into an operation: `operation_call`, with refusals as HTTP statuses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from loregarden.services.dependency_hold import DependencyHold
from loregarden.services.operations import (
    Caller,
    OperationId,
    OperationUnavailable,
    Transport,
    run_operation,
)
from loregarden.services.run_resolution import RunNotFound
from loregarden.services.ticket_ids import TicketNotFound
from loregarden.services.work_admission import WorkQueued
from pydantic import ValidationError
from sqlmodel import Session

_REST = Caller(transport=Transport.REST)


def run_rest_operation(
    session: Session, operation_id: OperationId, arguments: Mapping[str, Any]
) -> Any:
    """Run an operation for a REST route.

    The handlers refuse the way the rest of the codebase does, with a
    `ValueError` carrying the reason; here that reads as a 400 rather than a
    500, a ticket or run that is not there as a 404, and work that waits for
    a lane (or on a prerequisite) as a 409.
    """
    try:
        return run_operation(session, operation_id, arguments, _REST)
    except OperationUnavailable as exc:
        raise HTTPException(404, str(exc)) from exc
    except TicketNotFound as exc:
        raise HTTPException(404, str(exc) or "Ticket not found") from exc
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except WorkQueued as queued:
        raise HTTPException(409, detail=queued.as_dict()) from queued
    except DependencyHold as hold:
        # 409, as on the ticket routes: the same request with
        # ignore_prerequisites=true starts it.
        raise HTTPException(409, str(hold)) from hold
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
