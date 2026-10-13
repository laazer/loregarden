"""Unwrap artifacts whose `content_json` is a JSON string holding an object.

`loregarden_attach_artifact` / `loregarden_attach_evidence` took any JSON, so an
agent that encoded its object twice stored `"{\\"finding\\": …}"`. Every reader
treats `content_json` as an object, and one such `log` row made
`GET /api/tickets/{id}` raise `'str' object has no attribute 'get'` on every
load. On 2026-10-13 the live database held four (one `log`, three `evidence`),
each a string that parses to an object, so unwrapping loses nothing. The tools
now refuse a non-object; anything this cannot unwrap is reported, not guessed at.
"""

from __future__ import annotations

import logging

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

logger = logging.getLogger(__name__)

_DOUBLE_ENCODED = (
    "json_valid(content_json) AND json_type(content_json) = 'text' "
    "AND json_valid(json_extract(content_json, '$')) "
    "AND json_type(json_extract(content_json, '$')) = 'object'"
)


@migration("20261013_artifact_content_objects", after="20261013_agent_run_failure_kind")
def m_artifact_content_objects(conn: Connection) -> None:
    if not table_exists(conn, "artifacts"):
        return
    conn.execute(
        text(
            "UPDATE artifacts SET content_json = json_extract(content_json, '$') "
            f"WHERE {_DOUBLE_ENCODED}"
        )
    )
    leftover = (
        conn.execute(
            text(
                "SELECT id FROM artifacts WHERE json_valid(content_json) "
                "AND json_type(content_json) <> 'object'"
            )
        )
        .scalars()
        .all()
    )
    if leftover:
        logger.warning(
            "Artifacts whose content_json is not a JSON object and could not be "
            "unwrapped (readers will fail on them): %s",
            ", ".join(leftover),
        )
