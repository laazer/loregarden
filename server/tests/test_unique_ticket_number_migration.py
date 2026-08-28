"""A duplicate ticket number cannot be stored.

`services.ticket_ids.resolve` matches a shared id on its trailing number, so two
tickets sharing a number in one workspace make every link to either ambiguous.
Application code issued the number and nothing enforced it, which is how 38
tickets once landed carrying the column default of 0.
"""

from __future__ import annotations

import pytest
from loregarden.db.migration_utils import index_exists
from loregarden.db.migrations_ticket_ids import TICKET_NUMBER_INDEX, m_unique_ticket_number
from sqlalchemy import create_engine, text


def _tickets_table(conn) -> None:
    conn.execute(
        text("CREATE TABLE tickets (id TEXT PRIMARY KEY, workspace_id TEXT, ticket_number INTEGER)")
    )


def _insert(conn, tid: str, workspace: str, number: int) -> None:
    conn.execute(
        text("INSERT INTO tickets (id, workspace_id, ticket_number) VALUES (:i, :w, :n)"),
        {"i": tid, "w": workspace, "n": number},
    )


def test_index_is_created_and_blocks_a_duplicate_number() -> None:
    with create_engine("sqlite://").connect() as conn:
        _tickets_table(conn)
        _insert(conn, "a", "ws1", 501)
        m_unique_ticket_number(conn)
        assert index_exists(conn, TICKET_NUMBER_INDEX)
        with pytest.raises(Exception):
            _insert(conn, "b", "ws1", 501)


def test_the_unissued_sentinel_is_not_constrained() -> None:
    """Zero means not-yet-issued, and the backfill rewinds a whole workspace to it.

    Repeated zeros are therefore legal, and this index does not catch the
    stale-build write that produced them — the CLI refusal does. What the index
    guarantees is that no two tickets share an ISSUED number, which is the
    invariant `resolve` depends on.
    """
    with create_engine("sqlite://").connect() as conn:
        _tickets_table(conn)
        m_unique_ticket_number(conn)
        _insert(conn, "a", "ws1", 0)
        _insert(conn, "b", "ws1", 0)


def test_the_same_number_in_another_workspace_is_fine() -> None:
    with create_engine("sqlite://").connect() as conn:
        _tickets_table(conn)
        m_unique_ticket_number(conn)
        _insert(conn, "a", "ws1", 501)
        _insert(conn, "b", "ws2", 501)


def test_existing_duplicates_raise_rather_than_being_renumbered() -> None:
    with create_engine("sqlite://").connect() as conn:
        _tickets_table(conn)
        _insert(conn, "a", "ws1", 7)
        _insert(conn, "b", "ws1", 7)
        with pytest.raises(RuntimeError):
            m_unique_ticket_number(conn)


def test_running_twice_is_a_no_op() -> None:
    with create_engine("sqlite://").connect() as conn:
        _tickets_table(conn)
        m_unique_ticket_number(conn)
        m_unique_ticket_number(conn)
        assert index_exists(conn, TICKET_NUMBER_INDEX)
