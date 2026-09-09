"""Every SQLite connection in the process gets the same pragmas.

`lg-workflow-integrity-687`. Foreign-key enforcement was deliberately moved to
the `Engine` class so that per-test engines and engines opened by scripts get it
too, and its docstring says why: "a pragma that only some connections set is
worse than none: it makes enforcement depend on which code path opened the
connection."

The listener directly beneath it — journal mode and lock patience — was still
bound to this module's engine instance. So a script, a worker or a per-test
engine enforced foreign keys while falling back to the driver's 5-second lock
timeout instead of 30. That is not academic here: several agents work in sibling
worktrees of one repository at once, so multi-writer contention on a single
SQLite file is the normal operating condition, and a run has already been lost
to it.
"""

from __future__ import annotations

from pathlib import Path

import loregarden.db.session  # noqa: F401  — importing registers the listeners
from sqlalchemy import create_engine, text


def _pragmas(url: str) -> dict[str, object]:
    engine = create_engine(url)
    with engine.connect() as conn:
        return {
            name: conn.execute(text(f"PRAGMA {name}")).scalar()
            for name in ("journal_mode", "busy_timeout", "foreign_keys")
        }


def test_an_engine_that_is_not_the_module_singleton_still_waits_30_seconds(tmp_path: Path):
    """The defect. This engine is not `session.engine`, which is the whole point:
    scripts, workers and the test suite all build their own."""
    pragmas = _pragmas(f"sqlite:///{tmp_path / 'other.db'}")
    assert pragmas["busy_timeout"] == 30000, "a second engine fell back to the 5s driver default"
    assert pragmas["journal_mode"] == "wal"


def test_foreign_keys_are_on_there_too(tmp_path: Path):
    """The listener that was already correct, asserted beside the one that was
    not — so a later change that moves either back to an instance fails here."""
    assert _pragmas(f"sqlite:///{tmp_path / 'fk.db'}")["foreign_keys"] == 1


def test_an_in_memory_database_is_configured_without_a_path(tmp_path: Path):
    """An in-memory database reports an empty filename. The old code read the
    MODULE engine's URL rather than the connection's, so it judged every
    connection by the singleton's path; reading it from the connection means
    there is now a case with no path at all, and it must not raise."""
    pragmas = _pragmas("sqlite://")
    assert pragmas["busy_timeout"] == 30000
    assert pragmas["journal_mode"] == "memory"


def test_the_path_comes_from_the_connection_not_the_module_engine():
    """`PRAGMA database_list` names the file THIS connection opened. Reading the
    module engine's URL instead is how a second engine got configured for a file
    it had never opened — harmless for `busy_timeout`, wrong for the iCloud
    journal-mode branch, which exists to keep WAL sidecars off a syncing folder.
    """
    from loregarden.db.session import _connection_db_path

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()
        assert _connection_db_path(cursor) is None  # in-memory has no file
        cursor.close()


# --- lg-workflow-integrity-687: the write that lost the lock -----------------


def test_a_small_log_persists_exactly_as_often_as_before():
    """The floor is the old behaviour. Early in a run the log is short, someone
    may be watching it land, and nothing about that was the problem."""
    from loregarden.services.run_log_stream import RunLogStreamer

    streamer = RunLogStreamer(run_id="r", ticket_id="t", run_code="c", agent_id="a", skill_name="")
    assert streamer._persist_interval() == RunLogStreamer.PERSIST_INTERVAL_SECONDS
    streamer._last_persist_bytes = 75_000
    assert streamer._persist_interval() == RunLogStreamer.PERSIST_INTERVAL_SECONDS


def test_a_long_log_is_rewritten_less_often():
    """The log is one row rewritten in full, so the cost of a write grows with
    the run while the rate stayed fixed. A 320KB row at 2.5 writes a second is
    800KB/s of rewrite; that is the pressure a write lost a lock under."""
    from loregarden.services.run_log_stream import RunLogStreamer

    streamer = RunLogStreamer(run_id="r", ticket_id="t", run_code="c", agent_id="a", skill_name="")
    streamer._last_persist_bytes = 320_000
    interval = streamer._persist_interval()
    assert interval > RunLogStreamer.PERSIST_INTERVAL_SECONDS
    throughput = streamer._last_persist_bytes / interval
    assert throughput <= RunLogStreamer.PERSIST_BYTES_PER_SECOND * 1.05


def test_the_write_budget_holds_as_the_log_grows():
    """The property, rather than three magic numbers: bytes per second stays
    bounded however long the run goes on."""
    from loregarden.services.run_log_stream import RunLogStreamer

    streamer = RunLogStreamer(run_id="r", ticket_id="t", run_code="c", agent_id="a", skill_name="")
    for size in (200_000, 500_000, 2_000_000):
        streamer._last_persist_bytes = size
        assert size / streamer._persist_interval() <= RunLogStreamer.PERSIST_BYTES_PER_SECOND * 1.05
