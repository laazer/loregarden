"""Migration 0058 collapses the per-workspace slot pools into one.

The risk it carries is not the schema change but the live rows: a slot with a
running agent in it must survive, because nothing else knows how to release
that agent's slot when it finishes. Free slots are scaffolding and are meant to
go.
"""

import tempfile

from loregarden.db.migrations_queue import m_global_agent_slots
from sqlalchemy import create_engine, text


def _db_with_slots(rows: list[tuple[str, str, int, int, str | None]]):
    """`rows` are (id, workspace_id, slot_number, is_available, current_run_id)."""
    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/t.db")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE agent_slots ("
                "  id TEXT PRIMARY KEY,"
                "  workspace_id TEXT NOT NULL,"
                "  slot_number INTEGER,"
                "  is_available INTEGER,"
                "  current_run_id TEXT,"
                "  assigned_at TEXT,"
                "  released_at TEXT"
                ")"
            )
        )
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO agent_slots "
                    "(id, workspace_id, slot_number, is_available, current_run_id) "
                    "VALUES (:id, :ws, :n, :free, :run)"
                ),
                {"id": row[0], "ws": row[1], "n": row[2], "free": row[3], "run": row[4]},
            )
    return engine


def _slots(engine) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, workspace_id, slot_number, is_available, current_run_id "
                "FROM agent_slots ORDER BY slot_number"
            )
        ).mappings()
        return [dict(row) for row in rows]


def test_free_slots_are_dropped_and_occupied_ones_survive():
    engine = _db_with_slots(
        [
            ("s1", "ws-a", 1, 0, "run-a"),
            ("s2", "ws-a", 2, 1, None),
            ("s3", "ws-b", 1, 0, "run-b"),
            ("s4", "ws-b", 2, 1, None),
        ]
    )

    with engine.begin() as conn:
        m_global_agent_slots(conn)

    slots = _slots(engine)

    # Both running agents kept a slot to be released from.
    assert {slot["current_run_id"] for slot in slots} == {"run-a", "run-b"}
    # The free scaffolding is gone; initialize_slots rebuilds the shared pool.
    assert len(slots) == 2


def test_survivors_join_the_shared_pool_without_duplicate_numbers():
    """Two workspaces each had a slot 1. One pool cannot."""
    engine = _db_with_slots(
        [
            ("s1", "ws-a", 1, 0, "run-a"),
            ("s2", "ws-b", 1, 0, "run-b"),
            ("s3", "ws-c", 1, 0, "run-c"),
        ]
    )

    with engine.begin() as conn:
        m_global_agent_slots(conn)

    slots = _slots(engine)

    assert [slot["slot_number"] for slot in slots] == [1, 2, 3]
    assert all(slot["workspace_id"] is None for slot in slots)


def test_an_all_idle_pool_collapses_to_nothing():
    engine = _db_with_slots(
        [
            ("s1", "ws-a", 1, 1, None),
            ("s2", "ws-a", 2, 1, None),
            ("s3", "ws-b", 1, 1, None),
        ]
    )

    with engine.begin() as conn:
        m_global_agent_slots(conn)

    assert _slots(engine) == []


def test_is_a_no_op_without_the_table():
    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/t.db")

    with engine.begin() as conn:
        m_global_agent_slots(conn)  # must not raise
