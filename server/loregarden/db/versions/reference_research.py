"""Research threads and capability claims (`models.domain.survey_research_tables`).

Adds `baxter_chat_sessions.mode`, and creates `survey_research_questions`,
`survey_research_candidates`, `survey_capability_claims` and
`survey_source_research`. Every existing thread stays `general`.
"""

from __future__ import annotations

from loregarden.db.migration_utils import add_columns_if_missing, index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

_TABLES: dict[str, str] = {
    "survey_research_questions": (
        "CREATE TABLE survey_research_questions ("
        "id VARCHAR NOT NULL PRIMARY KEY, "
        "workspace_id VARCHAR NOT NULL REFERENCES workspaces(id), "
        "thread_id VARCHAR NOT NULL REFERENCES baxter_chat_sessions(id), "
        "area_id VARCHAR REFERENCES survey_areas(id), "
        "question VARCHAR NOT NULL, "
        "capabilities_json VARCHAR NOT NULL DEFAULT '[]', "
        "interval_days INTEGER, "
        "run_count INTEGER NOT NULL DEFAULT 0, "
        "last_run_at DATETIME, "
        "saved BOOLEAN NOT NULL DEFAULT 0, "
        "archived_at DATETIME, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    ),
    "survey_research_candidates": (
        "CREATE TABLE survey_research_candidates ("
        "id VARCHAR NOT NULL PRIMARY KEY, "
        "question_id VARCHAR NOT NULL REFERENCES survey_research_questions(id), "
        "url VARCHAR NOT NULL, "
        "name VARCHAR NOT NULL, "
        "summary VARCHAR NOT NULL DEFAULT '', "
        "source_id VARCHAR REFERENCES survey_sources(id), "
        "licence_name VARCHAR NOT NULL DEFAULT '', "
        "language VARCHAR NOT NULL DEFAULT '', "
        "stars INTEGER, "
        "last_commit_at DATETIME, "
        "metadata_error VARCHAR NOT NULL DEFAULT '', "
        "first_seen_run INTEGER NOT NULL DEFAULT 0, "
        "last_seen_run INTEGER NOT NULL DEFAULT 0, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL, "
        "CONSTRAINT uq_survey_research_candidate UNIQUE (question_id, url))"
    ),
    "survey_capability_claims": (
        "CREATE TABLE survey_capability_claims ("
        "id VARCHAR NOT NULL PRIMARY KEY, "
        "url VARCHAR NOT NULL, "
        "source_id VARCHAR REFERENCES survey_sources(id), "
        "capability VARCHAR NOT NULL, "
        "status VARCHAR NOT NULL DEFAULT 'docs_only', "
        "evidence_path VARCHAR NOT NULL DEFAULT '', "
        "evidence_commit VARCHAR NOT NULL DEFAULT '', "
        "evidence_start INTEGER NOT NULL DEFAULT 0, "
        "evidence_end INTEGER NOT NULL DEFAULT 0, "
        "evidence_excerpt VARCHAR NOT NULL DEFAULT '', "
        "evidence_url VARCHAR NOT NULL DEFAULT '', "
        "note VARCHAR NOT NULL DEFAULT '', "
        "recorded_by VARCHAR NOT NULL DEFAULT 'agent', "
        "run_id VARCHAR NOT NULL DEFAULT '', "
        "question_id VARCHAR REFERENCES survey_research_questions(id), "
        "superseded_by VARCHAR REFERENCES survey_capability_claims(id), "
        "created_at DATETIME NOT NULL)"
    ),
    "survey_source_research": (
        "CREATE TABLE survey_source_research ("
        "source_id VARCHAR NOT NULL REFERENCES survey_sources(id), "
        "question_id VARCHAR NOT NULL REFERENCES survey_research_questions(id), "
        "added_at DATETIME NOT NULL, "
        "PRIMARY KEY (source_id, question_id))"
    ),
}

#: index name -> (table, columns)
_INDEXES: dict[str, tuple[str, str]] = {
    "ix_survey_research_questions_workspace_id": ("survey_research_questions", "workspace_id"),
    "ix_survey_research_questions_thread_id": ("survey_research_questions", "thread_id"),
    "ix_survey_research_questions_area_id": ("survey_research_questions", "area_id"),
    "ix_survey_research_candidates_question_id": ("survey_research_candidates", "question_id"),
    "ix_survey_research_candidates_url": ("survey_research_candidates", "url"),
    "ix_survey_research_candidates_source_id": ("survey_research_candidates", "source_id"),
    "ix_survey_capability_claims_url": ("survey_capability_claims", "url"),
    "ix_survey_capability_claims_source_id": ("survey_capability_claims", "source_id"),
    "ix_survey_capability_claims_question_id": ("survey_capability_claims", "question_id"),
}


@migration("20261111_reference_research", after="20261110_reference_areas")
def m_reference_research(conn: Connection) -> None:
    add_columns_if_missing(
        conn,
        "baxter_chat_sessions",
        {
            "mode": (
                "ALTER TABLE baxter_chat_sessions "
                "ADD COLUMN mode VARCHAR NOT NULL DEFAULT 'general'"
            )
        },
    )
    for name, statement in _TABLES.items():
        if not table_exists(conn, name):
            conn.execute(text(statement))
    for index, (table, columns) in _INDEXES.items():
        if table_exists(conn, table) and not index_exists(conn, index):
            conn.execute(text(f"CREATE INDEX {index} ON {table} ({columns})"))
