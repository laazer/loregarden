"""Operations: each thing the control plane does, declared once and reached by
every transport (MCP, the CLI, REST) through `operation_call`."""

from loregarden.services.operations.call import (
    OPERATIONS,
    STRICT_READS_ENV,
    Caller,
    operation_call,
    run_operation,
)
from loregarden.services.operations.spec import (
    Effect,
    Operation,
    OperationId,
    OperationUnavailable,
    ReadOperationWrote,
    Transport,
)

__all__ = [
    "OPERATIONS",
    "STRICT_READS_ENV",
    "Caller",
    "Effect",
    "Operation",
    "OperationId",
    "OperationUnavailable",
    "ReadOperationWrote",
    "Transport",
    "operation_call",
    "run_operation",
]
