"""Give unchecked-handoff notices their own artifact kind.

They were filed as `error`, so the ticket payload's latest-error field and the
Timeline's error counts read a notice ("no gate checked this handoff") as a
failure — about 200 rows across eight agent pairs on the live database. See
`ArtifactKind.HANDOFF_NOT_VALIDATED`.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from loregarden.db.versions import migration
from sqlalchemy import Connection, text

#: The title `handoff_writer._record_unvalidated_handoff` writes, as a LIKE
#: pattern. Spelled out rather than imported: a migration describes the rows as
#: they were written, and must keep matching them if the writer later changes.
_TITLE_PREFIX = "Handoff not validated — %"

#: Literal, not the enum: the rows were written with these exact strings.
_OLD_KIND = "error"
_NEW_KIND = "handoff_not_validated"


@migration("20261008_handoff_notice_kind", after="20261007_layout_by_question")
def m_handoff_notice_kind(conn: Connection) -> None:
    if not table_exists(conn, "artifacts"):
        return
    conn.execute(
        text(
            "UPDATE artifacts SET kind = :new_kind WHERE kind = :old_kind AND title LIKE :title_prefix"
        ),
        {"new_kind": _NEW_KIND, "old_kind": _OLD_KIND, "title_prefix": _TITLE_PREFIX},
    )
