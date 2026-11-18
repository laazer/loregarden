"""The symbol map's only database writer, and its three readers.

A build is a FULL replace of one checkout's partition, and the order is the
whole point: every row is computed BEFORE a single old row is deleted, in one
transaction. The other order turns a transient read failure into a map that
is gone, which is worse than a map that is one commit stale.

Nothing here shells git. `sources` is the package's single process seam and
hands this module the paths and the sha.

The interpreter guard fires FIRST, before anything is read or written.
`ast.parse` is only as new as the running interpreter — seven files in
`server/loregarden` fail under system 3.9 and parse clean under 3.11, and
this repo's hook scripts run under 3.9. A build on the wrong interpreter
would succeed, report a plausible row count, and lose whole modules; the
consumer whose entire contract is an honest NOT-FOUND would then report a
confident one.
"""

from __future__ import annotations

import difflib
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Generic, TypeVar
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.symbol_map_enums import (
    ExtractorTier,
    Freshness,
    ParseStatus,
    SourceLanguage,
    SymbolKind,
)
from loregarden.models.domain.symbol_map_tables import (
    SymbolMapFile,
    SymbolMapPartition,
    SymbolMapSymbol,
)
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.extract import extract
from loregarden.services.symbol_map.freshness import map_freshness, partition_for
from loregarden.services.symbol_map.sources import head_sha, tracked_files
from loregarden.services.symbol_map.values import FileExtraction, detect, tier_for
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete
from sqlmodel import Session, col, select

T = TypeVar("T")

#: `server/pyproject.toml` says `requires-python = ">=3.11"`.
_REQUIRED_PYTHON = (3, 11)

#: `difflib.get_close_matches`' own defaults. The two are coupled: the SQL
#: window below is derived from the cutoff, so changing one changes the other.
_CUTOFF = 0.6
_DEFAULT_LIMIT = 5

#: Select only the name column, and only the names whose length could possibly
#: reach the cutoff. `ratio = 2M/(L+n) <= 2*min(L,n)/(L+n)`, so a name outside
#: `[ceil(3L/7), floor(7L/3)]` cannot score 0.6 and can never have been a
#: suggestion: the window is provably lossless, not merely a good guess. No
#: symbol row is ever selected and the partition is never materialised.
_SUGGEST_SQL = (
    "SELECT DISTINCT name FROM symbol_map_symbols "
    "WHERE partition_id = ? AND length(name) BETWEEN ? AND ?"
)


class InterpreterTooOldError(RuntimeError):
    """The running interpreter is older than the sources it would be parsing."""


class BuildReport(BaseModel):
    """What one build actually did.

    A return value rather than a log line: a count nobody reads cannot tell a
    working build from a silent no-op, and these numbers are the measurement
    this feature has.
    """

    model_config = ConfigDict(frozen=True)

    files_seen: int
    symbol_rows: int
    unsupported_files: int
    parse_failures: int
    elapsed_seconds: float
    head_sha: str
    checkout_identity: str


@dataclass(frozen=True)
class SymbolLocation:
    """One definition site as a value, not as a live ORM row.

    Frozen and detached so a consumer cannot lazy-load through a closed
    session and get an exception where it expected a line number.
    """

    name: str
    qualified_name: str
    kind: SymbolKind
    path: str
    line: int
    parent: str
    extractor_tier: ExtractorTier


@dataclass(frozen=True)
class MapRead(Generic[T]):
    """Rows and the verdict on whether to trust them, together.

    No public reader returns bare rows. A `map_freshness()` the caller is
    trusted to call first is a convention; this is a contract.
    """

    rows: T
    freshness: Freshness


def _require_a_new_enough_interpreter() -> None:
    running = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info[:2] < _REQUIRED_PYTHON:
        raise InterpreterTooOldError(
            f"the symbol map needs Python 3.11 or newer to parse this repository's "
            f"sources; this interpreter is {running}. Building under an older one "
            f"records modern syntax as unparseable and loses whole modules silently."
        )


def _extraction_for(repo_root: Path, path: str) -> FileExtraction:
    """One tracked path's contribution, without ever letting a read escape.

    A file of an unsupported language is NEVER read — which is also why a
    binary path cannot raise a decode error. A supported one is, and git
    lists paths the filesystem will not produce bytes for: a file deleted but
    not yet committed as deleted (every mid-edit working tree) and a symlink
    whose target is gone. A read failure is a parse failure, recorded on the
    row and counted on the report; the alternative loses the whole map over
    one path.
    """
    language = detect(path)
    if language is SourceLanguage.OTHER:
        return FileExtraction(
            language=language,
            extractor_tier=tier_for(language),
            parse_status=ParseStatus.UNSUPPORTED_LANGUAGE,
        )
    try:
        data = (repo_root / path).read_bytes()
    except OSError as exc:
        return FileExtraction(
            language=language,
            extractor_tier=tier_for(language),
            parse_status=ParseStatus.PARSE_FAILED,
            parse_error=str(exc) or type(exc).__name__,
        )
    return extract(path, data)


def build_symbol_map(session: Session, workspace_id: str, repo_root: Path) -> BuildReport:
    """Build this checkout's map and return what it measured.

    *repo_root* is explicit and the package never reads the workspace's
    recorded path. One derivation, at the top of the request: the identity,
    the git calls and the file reads all take this same root, so a build
    invoked from a linked checkout cannot store that checkout's identity over
    the primary's line numbers.
    """
    _require_a_new_enough_interpreter()
    started = perf_counter()

    sha = head_sha(repo_root)
    identity = identify(repo_root)
    resolved_root = os.path.realpath(repo_root)

    # Everything is extracted before anything is deleted (AC2/AC5.8).
    extractions = [(path, _extraction_for(repo_root, path)) for path in tracked_files(repo_root)]

    stamped_at = utcnow()
    partition = partition_for(session, workspace_id, repo_root)
    if partition is None:
        partition = SymbolMapPartition(
            workspace_id=workspace_id,
            checkout_identity=identity,
            checkout_root=resolved_root,
            head_sha=sha,
            built_at=stamped_at,
        )
    else:
        session.execute(delete(SymbolMapSymbol).where(SymbolMapSymbol.partition_id == partition.id))
        session.execute(delete(SymbolMapFile).where(SymbolMapFile.partition_id == partition.id))
        partition.checkout_root = resolved_root
        partition.head_sha = sha
        partition.built_at = stamped_at
    session.add(partition)
    # The file rows carry `partition_id` as a plain string rather than through
    # a relationship, so the unit of work does not know the partition must be
    # inserted first; without this the foreign key fails on a first build.
    session.flush()

    file_rows: list[SymbolMapFile] = []
    symbol_rows: list[SymbolMapSymbol] = []
    for path, extraction in extractions:
        # The id is minted here rather than flushed out of the database: one
        # flush per file is 3,476 round trips on this repository alone.
        file_id = str(uuid4())
        file_rows.append(
            SymbolMapFile(
                id=file_id,
                partition_id=partition.id,
                path=path,
                language=extraction.language,
                extractor_tier=extraction.extractor_tier,
                parse_status=extraction.parse_status,
                parse_error=extraction.parse_error,
                indexed_sha=sha,
                index_built_at=stamped_at,
            )
        )
        symbol_rows.extend(
            SymbolMapSymbol(
                file_id=file_id,
                partition_id=partition.id,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                kind=symbol.kind,
                line=symbol.line,
                parent=symbol.parent,
            )
            for symbol in extraction.symbols
        )

    session.add_all(file_rows)
    session.add_all(symbol_rows)
    session.commit()

    statuses = [extraction.parse_status for _, extraction in extractions]
    return BuildReport(
        files_seen=len(extractions),
        symbol_rows=len(symbol_rows),
        unsupported_files=statuses.count(ParseStatus.UNSUPPORTED_LANGUAGE),
        parse_failures=statuses.count(ParseStatus.PARSE_FAILED),
        elapsed_seconds=perf_counter() - started,
        head_sha=sha,
        checkout_identity=identity,
    )


def _location(row: SymbolMapSymbol, path: str, tier: ExtractorTier) -> SymbolLocation:
    return SymbolLocation(
        name=row.name,
        qualified_name=row.qualified_name,
        kind=row.kind,
        path=path,
        line=row.line,
        parent=row.parent,
        extractor_tier=tier,
    )


def definitions_for(
    session: Session, workspace_id: str, repo_root: Path, name: str
) -> MapRead[list[SymbolLocation]]:
    """Every definition site for a bare *name*, ordered by (path, line).

    Deterministic and unranked: with no edges in the schema a ranker would
    ship a constant sort key, so 282 and 283 own ranking instead.
    """
    partition = partition_for(session, workspace_id, repo_root)
    if partition is None:
        return MapRead(rows=[], freshness=Freshness.NO_PARTITION)
    rows = session.exec(
        select(SymbolMapSymbol).where(
            SymbolMapSymbol.partition_id == partition.id,
            SymbolMapSymbol.name == name,
        )
    ).all()
    files = _file_rows_by_id(session, {row.file_id for row in rows})
    locations = sorted(
        (
            _location(row, files[row.file_id].path, files[row.file_id].extractor_tier)
            for row in rows
        ),
        key=lambda location: (location.path, location.line),
    )
    return MapRead(rows=locations, freshness=map_freshness(session, workspace_id, repo_root))


def _file_rows_by_id(session: Session, file_ids: set[str]) -> dict[str, SymbolMapFile]:
    if not file_ids:
        return {}
    rows = session.exec(select(SymbolMapFile).where(col(SymbolMapFile.id).in_(file_ids))).all()
    return {row.id: row for row in rows}


def outline_for(
    session: Session, workspace_id: str, repo_root: Path, path: str
) -> MapRead[list[SymbolLocation]]:
    """Every symbol recorded for one repo-relative path, ordered by line."""
    partition = partition_for(session, workspace_id, repo_root)
    if partition is None:
        return MapRead(rows=[], freshness=Freshness.NO_PARTITION)
    freshness = map_freshness(session, workspace_id, repo_root)
    file_row = session.exec(
        select(SymbolMapFile).where(
            SymbolMapFile.partition_id == partition.id,
            SymbolMapFile.path == path,
        )
    ).one_or_none()
    if file_row is None:
        return MapRead(rows=[], freshness=freshness)
    rows = session.exec(select(SymbolMapSymbol).where(SymbolMapSymbol.file_id == file_row.id)).all()
    locations = sorted(
        (_location(row, file_row.path, file_row.extractor_tier) for row in rows),
        key=lambda location: location.line,
    )
    return MapRead(rows=locations, freshness=freshness)


def _length_window(length: int) -> tuple[int, int]:
    """The lossless length window for a query of *length* at cutoff 0.6."""
    return math.ceil(3 * length / 7), (7 * length) // 3


def suggest_names(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    name: str,
    limit: int = _DEFAULT_LIMIT,
) -> MapRead[list[str]]:
    """The closest existing names to *name*, for catching invented identifiers.

    The narrowing happens in SQL, over the name column alone and under the
    provably lossless window above, so answering does not load the map.
    """
    partition = partition_for(session, workspace_id, repo_root)
    if partition is None:
        return MapRead(rows=[], freshness=Freshness.NO_PARTITION)
    shortest, longest = _length_window(len(name))
    candidates = sorted(
        row[0]
        for row in session.connection()
        .exec_driver_sql(_SUGGEST_SQL, (partition.id, shortest, longest))
        .fetchall()
    )
    matches = difflib.get_close_matches(name, candidates, limit, _CUTOFF)
    return MapRead(rows=matches, freshness=map_freshness(session, workspace_id, repo_root))
