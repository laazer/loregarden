"""REST's way into an operation: `operation_call`, with refusals as HTTP statuses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from loregarden.services.operations import (
    Caller,
    OperationId,
    OperationUnavailable,
    Transport,
    run_operation,
)
from loregarden.services.ticket_ids import TicketNotFound
from pydantic import ValidationError
from sqlmodel import Session

_REST = Caller(transport=Transport.REST)


def run_rest_operation(
    session: Session, operation_id: OperationId, arguments: Mapping[str, Any]
) -> Any:
    """Run an operation for a REST route.

    The handlers refuse the way the rest of the codebase does, with a
    `ValueError` carrying the reason; here that reads as a 400 rather than a
    500, and a ticket that is not there as a 404.
    """
    try:
        return run_operation(session, operation_id, arguments, _REST)
    except OperationUnavailable as exc:
        raise HTTPException(404, str(exc)) from exc
    except TicketNotFound as exc:
        raise HTTPException(404, str(exc) or "Ticket not found") from exc
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
