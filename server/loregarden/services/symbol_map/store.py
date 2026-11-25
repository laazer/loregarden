"""The symbol map's only database writer, and its three readers.

A build is a FULL replace of one checkout's partition, and the order is the
whole point: every row is computed BEFORE a single old row is deleted, in one
transaction. The other order turns a transient read failure into a map that
is gone, which is worse than a map that is one commit stale.

`apply_extractions` is the package's ONLY place that mints a `SymbolMapFile`
or a `SymbolMapSymbol`, and a full build is the special case of it where the
changed set is every tracked path and the deleted set is empty. Two loops is
the shape where a later extractor change reaches the build and misses the
incremental refresh — and nothing fails, because both still produce rows.

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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Generic, TypeVar
from uuid import uuid4

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.symbol_map_enums import (
    ExtractorTier,
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
from loregarden.services.symbol_map.freshness import MapStatus, partition_for, status_for
from loregarden.services.symbol_map.sources import head_sha, tracked_files
from loregarden.services.symbol_map.values import FileExtraction, detect, tier_for
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete
from sqlmodel import Session, col, select

T = TypeVar("T")

#: How many paths one `IN (...)` carries. SQLite caps bound parameters per
#: statement, and a refresh seeded three commits behind can name more paths
#: than the warm case ever does.
_PATH_CHUNK = 400

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

    No public reader returns bare rows. A `map_status()` the caller is
    trusted to call first is a convention; this is a contract. The verdict
    arrives with its own lag attached (lg-code-knowledge-284), so a consumer
    can say how far behind the answer is rather than only that it is stale.
    """

    rows: T
    status: MapStatus


def require_a_new_enough_interpreter() -> None:
    """Refuse before anything is read or written on too old an interpreter.

    Public because the incremental refresh fires it too: a warm pass never
    reaches `build_symbol_map`, and parsing modern syntax under 3.9 records
    whole modules as unparseable without raising anything.
    """
    running = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info[:2] < _REQUIRED_PYTHON:
        raise InterpreterTooOldError(
            f"the symbol map needs Python 3.11 or newer to parse this repository's "
            f"sources; this interpreter is {running}. Building under an older one "
            f"records modern syntax as unparseable and loses whole modules silently."
        )


def extraction_for(repo_root: Path, path: str) -> FileExtraction:
    """One tracked path's contribution, without ever letting a read escape.

    A file of an unsupported language is NEVER read — which is also why a
    binary path cannot raise a decode error. A supported one is, and git
    lists paths the filesystem will not produce bytes for: a symlink whose
    target is gone, a file without read permission, one deleted since it was
    listed. A read failure is a parse failure, recorded on the row and counted
    on the report; the alternative loses the whole map over one path.
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
    require_a_new_enough_interpreter()
    started = perf_counter()

    sha = head_sha(repo_root)
    identity = identify(repo_root)
    resolved_root = os.path.realpath(repo_root)

    # Everything is extracted before anything is deleted (AC2/AC5.8).
    extractions = [(path, extraction_for(repo_root, path)) for path in tracked_files(repo_root)]

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
        partition.checkout_root = resolved_root
    apply_extractions(
        session,
        partition,
        extractions,
        deleted=(),
        sha=sha,
        stamped_at=stamped_at,
        replace_everything=True,
    )

    symbol_rows = [symbol for _, extraction in extractions for symbol in extraction.symbols]
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


def _drop_paths(session: Session, partition_id: str, paths: set[str]) -> None:
    """Remove the file row and every symbol row for each of *paths*.

    Symbols first, so no symbol row can outlive the file row it belongs to —
    the one way a per-file delete-and-reinsert leaves rubbish that answers
    `definitions_for` from a file that no longer exists, at line numbers
    nothing will ever correct.
    """
    if not paths:
        return
    ordered = sorted(paths)
    for start in range(0, len(ordered), _PATH_CHUNK):
        chunk = ordered[start : start + _PATH_CHUNK]
        # The file ids are resolved by a SUBQUERY rather than read into Python
        # first. Both of these are DML, so SQLite opens the write transaction
        # on the first one and resolves the ids inside it; a SELECT run before
        # the transaction reads whatever a peer refresh had committed a moment
        # earlier, deletes ids that peer has already replaced, and then
        # collides on `uq_symbol_map_file_path` when it inserts its own.
        doomed = select(SymbolMapFile.id).where(
            SymbolMapFile.partition_id == partition_id,
            col(SymbolMapFile.path).in_(chunk),
        )
        session.execute(delete(SymbolMapSymbol).where(col(SymbolMapSymbol.file_id).in_(doomed)))
        session.execute(
            delete(SymbolMapFile).where(
                SymbolMapFile.partition_id == partition_id,
                col(SymbolMapFile.path).in_(chunk),
            )
        )


def apply_extractions(
    session: Session,
    partition: SymbolMapPartition,
    extractions: Sequence[tuple[str, FileExtraction]],
    *,
    deleted: Iterable[str],
    sha: str,
    stamped_at: datetime,
    replace_everything: bool = False,
) -> None:
    """Write one generation of rows for *extractions*, in ONE transaction.

    The package's single row-minting site (the full build and the incremental
    refresh are both this function), and the single place the partition's sha
    advances. Deletes come first and the commit comes last, so a failure
    anywhere in between leaves the OLD rows intact — never a file row with
    zero symbols.

    `replace_everything` is the full build: every row of the partition goes,
    including paths the tree no longer has. The incremental caller names its
    own paths instead, so a file nobody touched keeps its row AND its id,
    which is the only thing that distinguishes a refresh from a rebuild
    wearing the right return value.
    """
    if replace_everything:
        session.execute(delete(SymbolMapSymbol).where(SymbolMapSymbol.partition_id == partition.id))
        session.execute(delete(SymbolMapFile).where(SymbolMapFile.partition_id == partition.id))
    else:
        _drop_paths(session, partition.id, {path for path, _ in extractions} | set(deleted))

    partition.head_sha = sha
    partition.built_at = stamped_at
    # Cleared in the same transaction that writes the rows, so BUILDING ends
    # exactly when the index it describes becomes readable.
    partition.refresh_started_at = None
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
        return MapRead(rows=[], status=status_for(session, repo_root, None))
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
    return MapRead(rows=locations, status=status_for(session, repo_root, partition))


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
        return MapRead(rows=[], status=status_for(session, repo_root, None))
    status = status_for(session, repo_root, partition)
    file_row = session.exec(
        select(SymbolMapFile).where(
            SymbolMapFile.partition_id == partition.id,
            SymbolMapFile.path == path,
        )
    ).one_or_none()
    if file_row is None:
        return MapRead(rows=[], status=status)
    rows = session.exec(select(SymbolMapSymbol).where(SymbolMapSymbol.file_id == file_row.id)).all()
    locations = sorted(
        (_location(row, file_row.path, file_row.extractor_tier) for row in rows),
        key=lambda location: location.line,
    )
    return MapRead(rows=locations, status=status)


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
        return MapRead(rows=[], status=status_for(session, repo_root, None))
    shortest, longest = _length_window(len(name))
    candidates = sorted(
        row[0]
        for row in session.connection()
        .exec_driver_sql(_SUGGEST_SQL, (partition.id, shortest, longest))
        .fetchall()
    )
    matches = difflib.get_close_matches(name, candidates, limit, _CUTOFF)
    return MapRead(rows=matches, status=status_for(session, repo_root, partition))
