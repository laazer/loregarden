"""Retiring a partition whose checkout is gone (lg-code-knowledge-281).

The detector and its repair live here; `services/invariant_registry.py` holds
only the `Invariant(...)` entry naming them, following
`services/orphaned_worktree_rows.py`.

Registered EXPECTED with a repair rather than report-only. A report-only
invariant reports every violation on every pass forever, because nothing else
clears it, and this machine retires ephemeral checkouts constantly — that is
a backlog-finding firehose, not a safety measure.

Two refusals, both deliberate. If NO registered partition resolves to a
present directory, or if the filesystem will not answer about any one of
them, the whole pass refuses and records one finding instead of deleting
anything. An unmounted volume and a database opened on the wrong host both
look exactly like "every checkout is gone", and repairing on that reading
deletes every map there is. "Nothing to judge" and "I could not tell" are
different answers and a detector that collapses them means nothing when it
passes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC

from loregarden.models.domain.symbol_map_tables import (
    SymbolMapFile,
    SymbolMapPartition,
    SymbolMapSymbol,
)
from loregarden.services.directory_release import DirectoryState, directory_state
from loregarden.services.invariants import Violation
from sqlalchemy import delete
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: The subject a refusal is filed against. Never a partition id, so a repair
#: handed a refusal finds nothing of its own to act on and deletes nothing.
REFUSAL_SUBJECT = "symbol-map-partition-sweep"


def _refusal(detail: str) -> list[Violation]:
    """Build the one refusal finding, and record its reason where a person
    reads it.

    The log belongs HERE and not at the two call sites (AC6.6). An
    EXPECTED-with-repair invariant whose repair declines emits only
    "repaired 0 of N" from `services/invariants.py`, so the reason a pass
    refused is written nowhere else; putting it in the helper also means the
    next refusal arm anyone adds cannot ship silent. The word "refused" is
    load-bearing: `retire_orphaned_partitions` warns from this same logger
    when it KEEPS a row it could not recheck, and that is an ordinary pass,
    not a refusal.
    """
    reason = f"refused this pass: {detail}"
    logger.warning("Symbol map partition sweep %s", reason)
    return [
        Violation(
            subject_id=REFUSAL_SUBJECT,
            ticket_id=None,
            entered_at=None,
            detail=reason,
        )
    ]


def orphaned_partitions(session: Session) -> list[Violation]:
    """Partitions whose recorded checkout is not there any more.

    Enumerated from `symbol_map_partitions` and never from the rows that
    would be deleted, so a partition whose file rows are already gone is
    still judged.
    """
    rows = session.exec(select(SymbolMapPartition).order_by(SymbolMapPartition.id)).all()
    if not rows:
        # Nothing registered is not the same as nothing resolving: there is
        # no population to judge, so there is no finding either.
        return []

    absent: list[SymbolMapPartition] = []
    present = 0
    for row in rows:
        try:
            state = directory_state(row.checkout_root)
        except OSError as exc:
            # No log here: `_refusal` records it. Two records for one event
            # is the regression this placement exists to prevent.
            return _refusal(f"the filesystem would not answer for {row.checkout_root!r} ({exc})")
        if state is DirectoryState.ABSENT:
            absent.append(row)
        else:
            present += 1

    if present == 0 and len(rows) > 1:
        # A mass disappearance, which is what an unmounted volume or a database
        # opened on another host looks like — repairing on that reading deletes
        # every map there is. The "more than one" is load-bearing: with a single
        # registered partition, "all of them are absent" and "this one is
        # absent" are the same observation and carry no extra signal, and the
        # one absent checkout is the ordinary case this invariant exists for.
        return _refusal(
            f"all {len(rows)} registered checkouts resolved absent at once, which is what "
            "an unmounted volume or a database opened on another host looks like"
        )

    return [
        Violation(
            subject_id=row.id,
            ticket_id=None,
            entered_at=(row.built_at.replace(tzinfo=UTC) if row.built_at is not None else None),
            detail=(
                f"symbol map partition {row.checkout_identity} records checkout "
                f"{row.checkout_root}, which is not there any more"
            ),
        )
        for row in absent
    ]


def retire_orphaned_partitions(session: Session, violations: Sequence[Violation]) -> int:
    """Delete each confirmed orphan. Returns how many partitions were retired.

    Acts only on the violations it is handed — a repair that re-enumerates
    would delete on a pass that found nothing, the refusal path included.
    Each partition is its own transaction, symbol rows first, then file rows,
    then the partition row LAST, so an interrupted repair never leaves rows
    pointing at a partition that is gone.
    """
    retired = 0
    for violation in violations:
        if violation.subject_id == REFUSAL_SUBJECT:
            continue
        row = session.get(SymbolMapPartition, violation.subject_id)
        if row is None:
            continue
        try:
            # The world may have moved between detect and repair.
            if directory_state(row.checkout_root) is not DirectoryState.ABSENT:
                continue
        except OSError:
            logger.warning(
                "Symbol map partition %s at %s: the filesystem would not say whether it "
                "is there, so it is kept",
                row.id,
                row.checkout_root,
                exc_info=True,
            )
            continue
        session.execute(delete(SymbolMapSymbol).where(SymbolMapSymbol.partition_id == row.id))
        session.execute(delete(SymbolMapFile).where(SymbolMapFile.partition_id == row.id))
        session.delete(row)
        session.commit()
        retired += 1
    return retired
