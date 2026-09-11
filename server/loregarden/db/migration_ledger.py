"""What a database has applied, against what this build registers.

`assert_migration_ids_are_sound` already guards the registered list: duplicates,
and a shipped prefix that changed. It never looks at what a database actually
APPLIED, and that is where the evidence of a renumber lives — so its own
docstring names a failure ("a renumber applied to a migration that already ran
somewhere") that it cannot detect.

Branches here claim the next free number when they are written, and main moves
before they merge. A pre-push suite takes 13-50 minutes, so every branch races
whatever lands during its own verification: five collisions on 2026-09-10 alone.
Renumbering is therefore routine, and a renumbered migration runs a SECOND time
under its new id against any database that applied the old one.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel import Session


def _suffix(migration_id: str) -> str:
    """The part of an id that names what it does, without its number.

    `0117_run_log_lines_table` and `0118_run_log_lines_table` are the same
    migration renumbered; comparing whole ids cannot see that.
    """
    head, _, rest = migration_id.partition("_")
    return rest if head.isdigit() and rest else migration_id


@dataclass(frozen=True)
class LedgerOrphan:
    """An applied id this build no longer registers."""

    applied_id: str
    #: The id it appears to have been renumbered to, or "" when nothing
    #: registered shares its suffix — which means it was deleted, not renumbered.
    renumbered_to: str

    @property
    def was_renumbered(self) -> bool:
        return bool(self.renumbered_to)


def ledger_orphans(session: Session, registered: list[str]) -> list[LedgerOrphan]:
    """Applied ids absent from `registered`, classified by why.

    The two need different responses and must not be reported as one thing:

    - RENUMBERED: the same migration is registered under a different number, so
      it has now run twice against this database. Harmless only if it is
      idempotent.
    - DELETED: nothing registered shares its suffix. The migration was removed
      from the build, and whatever it did to this database is still there.

    Returns [] when the table does not exist — a database that has never run a
    migration has no ledger to be inconsistent with.
    """
    # `execute`, not `exec`: SQLModel's `exec` is typed for its own select
    # constructs and has no overload for a TextClause.
    rows = session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
    ).all()
    if not rows:
        return []

    applied = [row[0] for row in session.execute(text("SELECT id FROM schema_migrations")).all()]
    known = set(registered)
    by_suffix = {_suffix(item): item for item in registered}

    orphans = []
    for applied_id in sorted(applied):
        if applied_id in known:
            continue
        orphans.append(
            LedgerOrphan(
                applied_id=applied_id,
                renumbered_to=by_suffix.get(_suffix(applied_id), ""),
            )
        )
    return orphans
