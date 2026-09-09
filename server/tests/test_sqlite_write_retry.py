"""A write that loses a lock race is retried, not lost.

`lg-workflow-integrity-687`. A completed stage's write-back hit
`database is locked` and the whole orchestration was discarded — 78 minutes of
finished work, recoverable only because the agent had committed to git first.
`busy_timeout` alone did not save it, so the write is retried.

The retry is deliberately narrow: contention only, a handful of attempts, and it
re-raises when they are spent. A retry that swallowed the failure would report a
success the run never had.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.services.sqlite_retry import (
    MAX_ATTEMPTS,
    is_locked_error,
    sqlite_write_retry,
)
from sqlalchemy.exc import OperationalError


def _locked() -> OperationalError:
    return OperationalError("UPDATE artifacts SET ...", {}, Exception("database is locked"))


def _syntax_error() -> OperationalError:
    return OperationalError("SELECT nope", {}, Exception("no such column: nope"))


def test_a_write_that_succeeds_first_time_is_not_retried():
    calls = []

    def write() -> str:
        calls.append(1)
        return "done"

    assert sqlite_write_retry(write) == "done"
    assert len(calls) == 1


def test_a_lock_failure_is_retried_and_then_succeeds():
    """The case that would have saved the lost run."""
    attempts = []

    def write() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise _locked()
        return "eventually"

    with mock.patch("loregarden.services.sqlite_retry.time.sleep"):
        assert sqlite_write_retry(write) == "eventually"
    assert len(attempts) == 3


def test_a_lock_failure_that_never_clears_re_raises():
    """AC3's other half. The work is not silently dropped: when the attempts are
    spent the caller sees the error, so a stuck writer is a visible fault rather
    than a run that quietly reports success."""
    attempts = []

    def write() -> None:
        attempts.append(1)
        raise _locked()

    with mock.patch("loregarden.services.sqlite_retry.time.sleep"):
        with pytest.raises(OperationalError):
            sqlite_write_retry(write)
    assert len(attempts) == MAX_ATTEMPTS


def test_an_error_that_is_not_contention_is_not_retried():
    """Retrying a schema fault hides it behind a delay and still fails."""
    attempts = []

    def write() -> None:
        attempts.append(1)
        raise _syntax_error()

    with pytest.raises(OperationalError):
        sqlite_write_retry(write)
    assert len(attempts) == 1, "a non-lock error must fail on the first attempt"


def test_the_backoff_actually_waits_between_attempts():
    """Retrying instantly into a held lock is just four failures in a row."""
    slept: list[float] = []

    def write() -> None:
        raise _locked()

    with mock.patch("loregarden.services.sqlite_retry.time.sleep", slept.append):
        with pytest.raises(OperationalError):
            sqlite_write_retry(write)

    assert len(slept) == MAX_ATTEMPTS - 1
    assert slept == sorted(slept), "backoff must not shrink between attempts"
    assert sum(slept) < 1.0, "total backoff should stay under a second"


def test_both_sqlite_lock_messages_are_recognised():
    """SQLite reports table-level and database-level contention through the same
    exception type; missing one means that half is never retried."""
    assert is_locked_error(_locked())
    assert is_locked_error(
        OperationalError("x", {}, Exception("database table is locked: artifacts"))
    )
    assert not is_locked_error(_syntax_error())
