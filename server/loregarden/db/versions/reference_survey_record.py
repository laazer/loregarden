"""The reference survey's record, moved out of a gitignored scratch file.

See `models.domain.survey_tables`. The DDL is frozen here as it stood when the
tables landed, so a later edit to the models cannot change what this migration
did. Nothing is backfilled: the legacy import is a separate, explicit command
(`loregarden survey import-legacy`), run once against the scratch files.
"""

from __future__ import annotations

from loregarden.db.migration_utils import index_exists, table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

#: In dependency order: every table follows the tables its foreign keys name.
_TABLES: tuple[tuple[str, str], ...] = (
    (
        "survey_legacy_sections",
        "CREATE TABLE survey_legacy_sections ( id VARCHAR NOT NULL, origin VARCHAR NOT NULL, "
        "ordinal INTEGER NOT NULL, heading VARCHAR NOT NULL, body VARCHAR NOT NULL, sha256 "
        "VARCHAR NOT NULL, imported_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT "
        "ux_survey_legacy_sections UNIQUE (origin, ordinal) )",
    ),
    (
        "survey_sources",
        "CREATE TABLE survey_sources ( id VARCHAR NOT NULL, workspace_id VARCHAR NOT NULL, kind "
        "VARCHAR(12) NOT NULL, url VARCHAR NOT NULL, name VARCHAR NOT NULL, question VARCHAR "
        "NOT NULL, queued_reason VARCHAR NOT NULL, priority INTEGER NOT NULL, status VARCHAR(8) "
        "NOT NULL, approval_id VARCHAR, licence_kind VARCHAR(7), licence_basis VARCHAR(16), "
        "licence_use VARCHAR(13), licence_name VARCHAR NOT NULL, licence_constraint VARCHAR NOT "
        "NULL, pinned_commit VARCHAR NOT NULL, fetched_at DATETIME, mapped_commit VARCHAR NOT "
        "NULL, refused_reason VARCHAR NOT NULL, notes VARCHAR NOT NULL, created_at DATETIME NOT "
        "NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT ux_survey_sources_url "
        "UNIQUE (url), FOREIGN KEY(workspace_id) REFERENCES workspaces (id), FOREIGN "
        "KEY(approval_id) REFERENCES approvals (id) )",
    ),
    (
        "survey_source_workspaces",
        "CREATE TABLE survey_source_workspaces ( source_id VARCHAR NOT NULL, workspace_id "
        "VARCHAR NOT NULL, question VARCHAR NOT NULL, queued_reason VARCHAR NOT NULL, added_at "
        "DATETIME NOT NULL, PRIMARY KEY (source_id, workspace_id), FOREIGN KEY(source_id) "
        "REFERENCES survey_sources (id), FOREIGN KEY(workspace_id) REFERENCES workspaces (id) )",
    ),
    (
        "survey_passes",
        "CREATE TABLE survey_passes ( id VARCHAR NOT NULL, source_id VARCHAR NOT NULL, ordinal "
        "INTEGER NOT NULL, label VARCHAR NOT NULL, mode VARCHAR(12) NOT NULL, status "
        "VARCHAR(10) NOT NULL, question VARCHAR NOT NULL, slice_json VARCHAR NOT NULL, "
        "file_budget INTEGER NOT NULL, read_commit VARCHAR NOT NULL, outcome VARCHAR(10), "
        "outcome_note VARCHAR NOT NULL, stopped_at VARCHAR NOT NULL, refused_reason VARCHAR NOT "
        "NULL, carrier_ticket_id VARCHAR, legacy_section_id VARCHAR, legacy_text VARCHAR NOT "
        "NULL, queued_at DATETIME NOT NULL, dispatched_at DATETIME, finished_at DATETIME, "
        "PRIMARY KEY (id), CONSTRAINT ux_survey_passes_ordinal UNIQUE (source_id, ordinal), "
        "FOREIGN KEY(source_id) REFERENCES survey_sources (id), FOREIGN KEY(carrier_ticket_id) "
        "REFERENCES tickets (id), FOREIGN KEY(legacy_section_id) REFERENCES "
        "survey_legacy_sections (id) )",
    ),
    (
        "survey_coverage",
        "CREATE TABLE survey_coverage ( id VARCHAR NOT NULL, source_id VARCHAR NOT NULL, path "
        "VARCHAR NOT NULL, in_map BOOLEAN NOT NULL, file_count INTEGER, byte_count INTEGER, "
        "mapped_commit VARCHAR NOT NULL, priority INTEGER, note VARCHAR NOT NULL, PRIMARY KEY "
        "(id), CONSTRAINT ux_survey_coverage_path UNIQUE (source_id, path), FOREIGN "
        "KEY(source_id) REFERENCES survey_sources (id) )",
    ),
    (
        "survey_pass_reads",
        "CREATE TABLE survey_pass_reads ( id VARCHAR NOT NULL, pass_id VARCHAR NOT NULL, "
        "coverage_id VARCHAR NOT NULL, state VARCHAR(7) NOT NULL, files_read INTEGER NOT NULL, "
        "note VARCHAR NOT NULL, PRIMARY KEY (id), CONSTRAINT ux_survey_pass_reads UNIQUE "
        "(pass_id, coverage_id), FOREIGN KEY(pass_id) REFERENCES survey_passes (id), FOREIGN "
        "KEY(coverage_id) REFERENCES survey_coverage (id) )",
    ),
    (
        "survey_evidence",
        "CREATE TABLE survey_evidence ( id VARCHAR NOT NULL, source_id VARCHAR NOT NULL, "
        "pass_id VARCHAR, epistemic VARCHAR(8) NOT NULL, operation VARCHAR NOT NULL, result "
        "VARCHAR NOT NULL, authority VARCHAR NOT NULL, limitations VARCHAR NOT NULL, "
        "residual_unknowns VARCHAR NOT NULL, corpus_json VARCHAR NOT NULL, corpus_complete "
        "BOOLEAN NOT NULL, completeness_basis VARCHAR NOT NULL, examined_count INTEGER, "
        "total_count INTEGER, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN "
        "KEY(source_id) REFERENCES survey_sources (id), FOREIGN KEY(pass_id) REFERENCES "
        "survey_passes (id) )",
    ),
    (
        "survey_findings",
        "CREATE TABLE survey_findings ( id VARCHAR NOT NULL, source_id VARCHAR NOT NULL, "
        "pass_id VARCHAR, claim VARCHAR(9) NOT NULL, epistemic VARCHAR(8) NOT NULL, statement "
        "VARCHAR NOT NULL, status VARCHAR(9) NOT NULL, retracted_reason VARCHAR NOT NULL, "
        "replaced_by_id VARCHAR, retracted_at DATETIME, created_at DATETIME NOT NULL, PRIMARY "
        "KEY (id), FOREIGN KEY(source_id) REFERENCES survey_sources (id), FOREIGN KEY(pass_id) "
        "REFERENCES survey_passes (id), FOREIGN KEY(replaced_by_id) REFERENCES survey_findings "
        "(id) )",
    ),
    (
        "survey_pass_tickets",
        "CREATE TABLE survey_pass_tickets ( id VARCHAR NOT NULL, pass_id VARCHAR NOT NULL, "
        "ticket_id VARCHAR, action VARCHAR(16) NOT NULL, finding_id VARCHAR, aimed_at_ticket_id "
        "VARCHAR, criteria_added INTEGER NOT NULL, verified BOOLEAN NOT NULL, legacy_ref "
        "VARCHAR NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), FOREIGN KEY(pass_id) "
        "REFERENCES survey_passes (id), FOREIGN KEY(ticket_id) REFERENCES tickets (id), FOREIGN "
        "KEY(finding_id) REFERENCES survey_findings (id), FOREIGN KEY(aimed_at_ticket_id) "
        "REFERENCES tickets (id) )",
    ),
    (
        "survey_finding_evidence",
        "CREATE TABLE survey_finding_evidence ( finding_id VARCHAR NOT NULL, evidence_id "
        "VARCHAR NOT NULL, PRIMARY KEY (finding_id, evidence_id), FOREIGN KEY(finding_id) "
        "REFERENCES survey_findings (id), FOREIGN KEY(evidence_id) REFERENCES survey_evidence "
        "(id) )",
    ),
    (
        "survey_unknowns",
        "CREATE TABLE survey_unknowns ( id VARCHAR NOT NULL, source_id VARCHAR NOT NULL, "
        "opened_by_pass_id VARCHAR, question VARCHAR NOT NULL, authority_required VARCHAR NOT "
        "NULL, status VARCHAR(8) NOT NULL, resolution VARCHAR NOT NULL, resolving_evidence_id "
        "VARCHAR, revision INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME "
        "NOT NULL, PRIMARY KEY (id), FOREIGN KEY(source_id) REFERENCES survey_sources (id), "
        "FOREIGN KEY(opened_by_pass_id) REFERENCES survey_passes (id), FOREIGN "
        "KEY(resolving_evidence_id) REFERENCES survey_evidence (id) )",
    ),
    (
        "survey_unknown_evidence",
        "CREATE TABLE survey_unknown_evidence ( unknown_id VARCHAR NOT NULL, evidence_id "
        "VARCHAR NOT NULL, stance VARCHAR(13) NOT NULL, PRIMARY KEY (unknown_id, evidence_id), "
        "FOREIGN KEY(unknown_id) REFERENCES survey_unknowns (id), FOREIGN KEY(evidence_id) "
        "REFERENCES survey_evidence (id) )",
    ),
)

_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "ix_survey_sources_status",
        "CREATE INDEX ix_survey_sources_status ON survey_sources (status)",
    ),
    (
        "ix_survey_sources_workspace_id",
        "CREATE INDEX ix_survey_sources_workspace_id ON survey_sources (workspace_id)",
    ),
    (
        "ix_survey_passes_carrier_ticket_id",
        "CREATE INDEX ix_survey_passes_carrier_ticket_id ON survey_passes (carrier_ticket_id)",
    ),
    (
        "ix_survey_passes_source_id",
        "CREATE INDEX ix_survey_passes_source_id ON survey_passes (source_id)",
    ),
    ("ix_survey_passes_status", "CREATE INDEX ix_survey_passes_status ON survey_passes (status)"),
    (
        "ix_survey_coverage_source_id",
        "CREATE INDEX ix_survey_coverage_source_id ON survey_coverage (source_id)",
    ),
    (
        "ix_survey_pass_reads_coverage_id",
        "CREATE INDEX ix_survey_pass_reads_coverage_id ON survey_pass_reads (coverage_id)",
    ),
    (
        "ix_survey_pass_reads_pass_id",
        "CREATE INDEX ix_survey_pass_reads_pass_id ON survey_pass_reads (pass_id)",
    ),
    (
        "ix_survey_evidence_pass_id",
        "CREATE INDEX ix_survey_evidence_pass_id ON survey_evidence (pass_id)",
    ),
    (
        "ix_survey_evidence_source_id",
        "CREATE INDEX ix_survey_evidence_source_id ON survey_evidence (source_id)",
    ),
    (
        "ix_survey_findings_pass_id",
        "CREATE INDEX ix_survey_findings_pass_id ON survey_findings (pass_id)",
    ),
    (
        "ix_survey_findings_source_id",
        "CREATE INDEX ix_survey_findings_source_id ON survey_findings (source_id)",
    ),
    (
        "ix_survey_findings_status",
        "CREATE INDEX ix_survey_findings_status ON survey_findings (status)",
    ),
    (
        "ix_survey_pass_tickets_pass_id",
        "CREATE INDEX ix_survey_pass_tickets_pass_id ON survey_pass_tickets (pass_id)",
    ),
    (
        "ix_survey_pass_tickets_ticket_id",
        "CREATE INDEX ix_survey_pass_tickets_ticket_id ON survey_pass_tickets (ticket_id)",
    ),
    (
        "ix_survey_unknowns_source_id",
        "CREATE INDEX ix_survey_unknowns_source_id ON survey_unknowns (source_id)",
    ),
    (
        "ix_survey_unknowns_status",
        "CREATE INDEX ix_survey_unknowns_status ON survey_unknowns (status)",
    ),
)


@migration("20261021_reference_survey_record", after="20261020_closed_tickets_close_workflows")
def m_reference_survey_record(conn: Connection) -> None:
    for name, ddl in _TABLES:
        if not table_exists(conn, name):
            conn.execute(text(ddl))
    for name, ddl in _INDEXES:
        if not index_exists(conn, name):
            conn.execute(text(ddl))
