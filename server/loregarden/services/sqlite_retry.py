"""Retry a SQLite write that lost a lock race, rather than losing the work.

`busy_timeout` covers a writer waiting for a lock it can see. It does not cover
`database is locked` surfacing anyway under contention, which is what killed a
78-minute orchestration on 2026-09-08: the write-back of a completed stage hit
the error and the run was lost even though the work was done
(lg-workflow-integrity-687).

Several agent sessions work in sibling worktrees against one SQLite file here,
so multi-writer contention is the normal operating condition, not an edge case.

Deliberately a function taking a callable rather than a context manager: a
`with` body cannot be re-executed, so a retrying context manager is a shape
that cannot work.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Total attempts, including the first. Small on purpose: a lock held longer
#: than this is a stuck writer, and retrying into it hides the real fault.
MAX_ATTEMPTS = 4

#: Base for exponential backoff — 50ms, 100ms, 200ms.
BACKOFF_SECONDS = 0.05


def is_locked_error(error: OperationalError) -> bool:
    """Whether this is contention rather than a real schema or syntax fault.

    Matched on the message because SQLite reports both `database is locked` and
    `database table is locked` through the same exception type, with no distinct
    code surfaced by the driver.
    """
    message = str(error).lower()
    return "database is locked" in message or "database table is locked" in message


def sqlite_write_retry(operation: Callable[[], T], *, what: str = "write") -> T:
    """Run `operation`, retrying only a lock-contention failure.

    `operation` must be self-contained — it is called again from the start on a
    retry, so it opens its own session and does its own commit.

    Anything that is not contention re-raises immediately, and a lock failure
    re-raises once the attempts are spent: a swallowed write here would report
    a success the run never had.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return operation()
        except OperationalError as exc:
            if not is_locked_error(exc) or attempt == MAX_ATTEMPTS:
                raise
            delay = BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "SQLite %s lost a lock race (attempt %d of %d); retrying in %.0fms",
                what,
                attempt,
                MAX_ATTEMPTS,
                delay * 1000,
            )
            time.sleep(delay)
    raise AssertionError("unreachable: the loop either returns or raises")
