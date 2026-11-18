"""Whether a stored map describes the tree the caller is standing on.

Its own module, not a corner of `store`, so 282, 283, 300 and 304 can import
the verdict without transitively importing the writer.

It COMPARES; it never derives a sha of its own. HEAD comes from
`sources.head_sha`, which is the package's single git seam.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.models.domain.symbol_map_enums import Freshness
from loregarden.models.domain.symbol_map_tables import SymbolMapPartition
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.sources import head_sha
from sqlmodel import Session, select


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


def map_freshness(session: Session, workspace_id: str, repo_root: Path) -> Freshness:
    """Three-valued, so a stale read and a genuine zero hit stay apart."""
    partition = partition_for(session, workspace_id, repo_root)
    if partition is None:
        return Freshness.NO_PARTITION
    if partition.head_sha == head_sha(repo_root):
        return Freshness.MATCHES
    return Freshness.SHA_MISMATCH
