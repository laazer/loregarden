"""`operation_call`: the one way any transport reaches an operation."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from loregarden.services.operations.disk_operations import DISK_OPERATIONS
from loregarden.services.operations.docker_capacity_operations import DOCKER_CAPACITY_OPERATIONS
from loregarden.services.operations.github_issue_operations import GITHUB_ISSUE_OPERATIONS
from loregarden.services.operations.initiatives import INITIATIVES
from loregarden.services.operations.memory_discredit import MEMORY_DISCREDIT
from loregarden.services.operations.memory_operations import MEMORY_OPERATIONS
from loregarden.services.operations.merge_queue_ops import MERGE_QUEUE_OPS
from loregarden.services.operations.organization_operations import ORGANIZATION_OPERATIONS
from loregarden.services.operations.pull_request_merges import PULL_REQUEST_MERGES
from loregarden.services.operations.research_record import RESEARCH_RECORD
from loregarden.services.operations.run_callbacks import RUN_CALLBACKS
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    OperationUnavailable,
    ReadOperationWrote,
)
from loregarden.services.operations.survey_queue import SURVEY_QUEUE
from loregarden.services.operations.ticket_links import TICKET_LINKS
from loregarden.services.operations.ticket_lists import TICKET_LISTS
from loregarden.services.operations.ticket_reads import TICKET_READS
from loregarden.services.operations.ticket_requeue import TICKET_REQUEUE
from loregarden.services.operations.ticket_writes import TICKET_WRITES
from pydantic import BaseModel
from sqlalchemy import event
from sqlmodel import Session

logger = logging.getLogger(__name__)

OPERATIONS: Mapping[OperationId, Operation] = {
    op.id: op
    for op in (
        *TICKET_READS,
        *TICKET_LISTS,
        *TICKET_LINKS,
        *RUN_CALLBACKS,
        *MERGE_QUEUE_OPS,
        *INITIATIVES,
        *TICKET_REQUEUE,
        *MEMORY_DISCREDIT,
        *SURVEY_QUEUE,
        *RESEARCH_RECORD,
        *MEMORY_OPERATIONS,
        *DOCKER_CAPACITY_OPERATIONS,
        *DISK_OPERATIONS,
        *GITHUB_ISSUE_OPERATIONS,
        *ORGANIZATION_OPERATIONS,
        *TICKET_WRITES,
        *PULL_REQUEST_MERGES,
    )
}

#: Set to "1" (the test suite does) to make a read operation that writes raise
#: instead of logging a warning.
STRICT_READS_ENV = "LOREGARDEN_STRICT_READ_OPERATIONS"


@contextmanager
def operation_call(
    session: Session, operation_id: OperationId, caller: Caller
) -> Iterator[Operation]:
    """Enter an operation on behalf of `caller`.

    Refuses a transport the operation is not offered on, and holds a read
    operation to writing nothing for as long as the block runs.
    """
    operation = OPERATIONS[operation_id]
    reason = operation.unavailable_on.get(caller.transport)
    if reason is not None:
        raise OperationUnavailable(operation_id, caller.transport, reason)
    if operation.effect is Effect.READ:
        with _writes_refused(session, operation_id):
            yield operation
    else:
        yield operation


def run_operation(
    session: Session,
    operation_id: OperationId,
    arguments: Mapping[str, Any],
    caller: Caller,
) -> Any:
    """Validate `arguments` against the operation's model, run it, and word the
    result for the caller's transport (`Operation.replies`)."""
    with operation_call(session, operation_id, caller) as operation:
        args: BaseModel = operation.args.model_validate(arguments)
        result = operation.handler(session, args, caller)
        reply = operation.replies.get(caller.transport)
        return result if reply is None else reply(session, result)


@contextmanager
def _writes_refused(session: Session, operation_id: OperationId) -> Iterator[None]:
    """Catch a read operation flushing changes.

    A read that writes moves `updated_at` with no event behind it and creates
    rows as a side effect (lg-workflow-integrity-606, lg-milestone-that-2196).
    Strict mode raises before the flush, so nothing is written. Otherwise the
    write goes ahead, since failing an operator's page over it would be worse,
    and the warning names the operation and what it changed so it is fixed.
    A raw `session.execute(update(...))` bypasses the flush and is not caught.
    """

    def before_flush(flushing: Session, _context: object, _instances: object) -> None:
        changed = sorted(
            {
                type(obj).__name__
                for obj in (*flushing.new, *flushing.deleted, *flushing.dirty)
                if obj in flushing.new or obj in flushing.deleted or flushing.is_modified(obj)
            }
        )
        if not changed:
            return
        if os.environ.get(STRICT_READS_ENV) == "1":
            raise ReadOperationWrote(f"read operation {operation_id} wrote {changed}")
        logger.warning("read operation %s wrote %s", operation_id, changed)

    event.listen(session, "before_flush", before_flush)
    try:
        yield
    finally:
        event.remove(session, "before_flush", before_flush)
