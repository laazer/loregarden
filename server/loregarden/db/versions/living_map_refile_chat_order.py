"""Re-file every chat map so its rows follow the reply's order.

Chat maps filed the records of one message in `source_ref` order, which put a
plan's steps in alphabetical order of step id and its questions ahead of it,
and filed plans with no topic row tying the steps to their card. Filing keeps
an item's `seq` forever, so fixing the filer leaves the rows already drawn
where they are. Every chat item is `source=record`, derived from the chat's
messages, so dropping them is lossless: the next read files them again, in
order and under their plan.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text


@migration("20261112_living_map_refile_chat_order", after="20261112_research_schedule")
def m_living_map_refile_chat_order(conn: Connection) -> None:
    if not table_exists(conn, "living_map_items"):
        return
    conn.execute(text("DELETE FROM living_map_items WHERE scope = 'chat' AND source = 'record'"))
