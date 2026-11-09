"""The gatekeeper rejects only on a named unmet criterion, with a complete finding.

Its role told it to treat every ticket as not ready by default and to prefer
rework when anything concerned it, which contradicts the done rule every other
judging stage follows and drove multi-round rework (`finding_bar`). Role bodies
are DB-authoritative, so the seed file's new decision guidelines are copied in
here (`refresh_role_from_seed`: skipped for a body a person has edited, which is
reported, and for a database with no agents yet).

The live body was edited in Studio three times in July 2026 (the full suite
pointed at `task hooks:server`, then a dedupe). The seed already carries both
edits and the live body lacks only later seed changes, so that exact body
(`SUPERSEDED`, by sha256) is refreshed. Any other edited body is left alone.
"""

from __future__ import annotations

from loregarden.db.migrations_role_refresh import refresh_role_from_seed
from loregarden.db.versions import migration
from sqlalchemy import Connection

#: Spelled again in the decorator: the shared-database guard reads ids from source literals.
MIGRATION_ID = "20261108_gatekeeper_rejects_on_criteria"
SLUG = "gatekeeper"
ROLE_FILE = "agents/15_gatekeeper_review/gatekeeper_review_v1.md"
MARKER = "Reject only on a **named unmet acceptance criterion**"
#: The live body as of 2026-11-08 (studio-ui version 4), compared with the seed by hand.
SUPERSEDED = frozenset({"99338bf4a76ce40557c609c5a4aea68b5731c2192534ba39dfea207e095d65a2"})


@migration("20261108_gatekeeper_rejects_on_criteria", after="20261108_rework_resume")
def m_gatekeeper_rejects_on_criteria(conn: Connection) -> None:
    refresh_role_from_seed(
        conn,
        migration_id=MIGRATION_ID,
        slug=SLUG,
        role_file=ROLE_FILE,
        marker=MARKER,
        superseded_bodies=SUPERSEDED,
    )
