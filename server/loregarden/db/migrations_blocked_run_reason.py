"""Mark blocked runs whose reason was never recorded, rather than leaving them blank.

`_complete_run` derived BLOCKED from the ticket's state and passed no message, so
29 of 74 blocked orchestration runs carry an empty `error_message` and cannot say
why they stopped (lg-workflow-integrity-90). The code path is fixed; these rows
are the ones already on disk.

They are marked, not invented. Where the ticket still carries `blocking_issues`
the reason is recoverable and gets copied across; where it does not — the field
is cleared when a ticket resumes — the row says so in as many words. An empty
string cannot distinguish "nobody wrote it down" from "there was nothing to
write", and that ambiguity is exactly what AC2 asks to remove.

Only rows that are still blank are touched, so a re-run changes nothing and no
recorded message is ever overwritten.
"""

from __future__ import annotations

from loregarden.db.migration_utils import table_exists
from sqlalchemy import text
from sqlalchemy.engine import Connection

_UNKNOWN = (
    "Reason not recorded. This run blocked before the orchestrator copied the "
    "ticket's blocking issue onto the run (lg-workflow-integrity-90)."
)


def m_blocked_run_reason(conn: Connection) -> None:
    if not table_exists(conn, "orchestration_runs") or not table_exists(conn, "tickets"):
        return

    # Where the ticket still holds the reason, it is the honest message.
    conn.execute(
        text(
            """
            UPDATE orchestration_runs
               SET error_message = (
                     SELECT t.blocking_issues FROM tickets t
                      WHERE t.id = orchestration_runs.ticket_id
                   )
             WHERE status = 'blocked'
               AND COALESCE(error_message, '') = ''
               AND COALESCE(
                     (SELECT t.blocking_issues FROM tickets t
                       WHERE t.id = orchestration_runs.ticket_id), ''
                   ) != ''
            """
        )
    )

    # The rest genuinely lost it. Say that, rather than leaving a blank that
    # reads as "no problem".
    conn.execute(
        text(
            "UPDATE orchestration_runs SET error_message = :marker "
            "WHERE status = 'blocked' AND COALESCE(error_message, '') = ''"
        ),
        {"marker": _UNKNOWN},
    )
