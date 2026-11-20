"""Whether a stored map describes the tree the caller is standing on.

Its own module, not a corner of `store`, so 282, 283, 300 and 304 can import
the verdict without transitively importing the writer.

It COMPARES; it never derives a sha of its own. HEAD comes from
`sources.head_sha`, which is the package's single git seam.

`map_status` NEVER raises (lg-code-knowledge-284). The verdict is what a
consumer falls back to live grep on, so an exception out of it fails the very
read it exists to make safe — and `head_sha` raises `UnbornHeadError` on a
checkout with no commit, which 281 let escape through all three readers.

The fresh path still costs exactly one `rev-parse`. `files_behind` runs the
delta oracle only once the verdict is ALREADY a mismatch or BUILDING, so the
surfaces this milestone exists to make cheap pay nothing for the lag number
they are now able to report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.symbol_map_enums import ChangedPathsOutcome, Freshness
from loregarden.models.domain.symbol_map_tables import SymbolMapFile, SymbolMapPartition
from loregarden.services.symbol_map import sources
from loregarden.services.symbol_map.checkout_identity import identify
from sqlalchemy import func
from sqlmodel import Session, select

#: What a read with no partition at all reports. `age_ms` has no referent
#: there, and 0 says so; a sentinel such as -1 would be a second way to spell
#: "no partition" that every consumer would have to learn.
_COLD = (0, 0, 0.0)


@dataclass(frozen=True)
class MapStatus:
    """What a query can honestly say about the index it just searched."""

    #: MATCHES | SHA_MISMATCH | NO_PARTITION | BUILDING.
    freshness: Freshness
    #: `now - partition.built_at`, floored at 0 so clock skew never produces a
    #: negative a renderer has no format for.
    age_ms: int
    #: How many paths the index is behind by. 0 when the sha matches.
    files_behind: int
    #: Indexed files over indexed-plus-behind: 1.0 when fresh, 0.0 when cold.
    #: Derived from rows at read time, never written by a progress loop.
    build_progress: float


def partition_for(
    session: Session, workspace_id: str, repo_root: Path
) -> SymbolMapPartition | None:
    """This workspace's partition for THIS checkout, or None.

    Keyed on the identity derived from the filesystem, so a database copied
    into another checkout finds no row rather than the originating
    checkout's.
    """
    return session.exec(
        select(SymbolMapPartition).where(
            SymbolMapPartition.workspace_id == workspace_id,
            SymbolMapPartition.checkout_identity == identify(repo_root),
        )
    ).one_or_none()


def _as_utc(stamp: datetime) -> datetime:
    """A stored timestamp as an aware one, the way every other reader here does.

    SQLAlchemy's SQLite `DateTime` drops the tzinfo on write, so `built_at` is
    stamped aware by `utcnow()` and reads back naive UTC. Subtracting the two
    is a TypeError on the READ path — raised, note, out of a function
    specified never to refuse.
    """
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _age_ms(built_at: datetime) -> int:
    # Floored at 0: this database travels between checkouts across a
    # filesystem whose clock nobody owns, and a negative age is a number no
    # renderer has a format for and no consumer can compare.
    return max(0, int((utcnow() - _as_utc(built_at)).total_seconds() * 1000))


def _verdict(partition: SymbolMapPartition, repo_root: Path) -> Freshness:
    """The four-valued comparison, with every way of not being able to look
    landing on a verdict rather than on an exception.

    The one answer it may not give is the confident one: MATCHES out of a
    comparison that never happened is the designed grep fallback never firing.
    """
    if partition.refresh_started_at is not None:
        return Freshness.BUILDING
    try:
        current = sources.head_sha(repo_root)
    except sources.UnbornHeadError:
        # No commit to compare against, so there is nothing this map can be
        # trusted about. Reported as the absence it is.
        return Freshness.NO_PARTITION
    except OSError:
        # git could not be run at all — a removed worktree is the ordinary
        # case, since partitions outlive their directory until the retirement
        # sweep runs.
        return Freshness.SHA_MISMATCH
    return Freshness.MATCHES if partition.head_sha == current else Freshness.SHA_MISMATCH


def _files_behind(partition: SymbolMapPartition, repo_root: Path) -> int:
    """How far the stored map is behind the tree, in paths.

    Reached only once the verdict is already a mismatch or BUILDING. A delta
    the oracle could not compute is reported as 0 rather than guessed at: the
    verdict already says the rows are not to be trusted, and an invented lag
    is a number an operator would act on.
    """
    delta = sources.changed_paths(repo_root, partition.head_sha)
    if delta.outcome is not ChangedPathsOutcome.OK:
        return 0
    return len(delta.changed | delta.deleted)


def _indexed_files(session: Session, partition_id: str) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(SymbolMapFile)
            .where(SymbolMapFile.partition_id == partition_id)
        )
        or 0
    )


def status_for(
    session: Session, repo_root: Path, partition: SymbolMapPartition | None
) -> MapStatus:
    """The status of an ALREADY-RESOLVED partition, so a reader that has one
    in hand does not look it up twice and risk the two disagreeing."""
    if partition is None:
        age_ms, files_behind, build_progress = _COLD
        return MapStatus(
            freshness=Freshness.NO_PARTITION,
            age_ms=age_ms,
            files_behind=files_behind,
            build_progress=build_progress,
        )
    freshness = _verdict(partition, repo_root)
    if freshness is Freshness.NO_PARTITION:
        age_ms, files_behind, build_progress = _COLD
        return MapStatus(
            freshness=freshness,
            age_ms=age_ms,
            files_behind=files_behind,
            build_progress=build_progress,
        )
    if freshness is Freshness.MATCHES:
        return MapStatus(
            freshness=freshness,
            age_ms=_age_ms(partition.built_at),
            files_behind=0,
            build_progress=1.0,
        )
    behind = _files_behind(partition, repo_root)
    indexed = _indexed_files(session, partition.id)
    total = indexed + behind
    return MapStatus(
        freshness=freshness,
        age_ms=_age_ms(partition.built_at),
        files_behind=behind,
        # A repository whose only commit is empty indexes nothing and is
        # behind by nothing. `0 / 0` is a ZeroDivisionError out of a function
        # specified never to refuse — and raised on the READ path, so it would
        # reach every consumer rather than the writer.
        build_progress=(indexed / total) if total else 1.0,
    )


def map_status(session: Session, workspace_id: str, repo_root: Path) -> MapStatus:
    """Four-valued, and carrying its own lag, so a stale read, a genuine zero
    hit and an index a peer is writing right now never collapse into one
    answer."""
    return status_for(session, repo_root, partition_for(session, workspace_id, repo_root))
