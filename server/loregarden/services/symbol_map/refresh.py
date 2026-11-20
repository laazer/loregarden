"""Keeping one checkout's map current without rebuilding it.

The writer 281 did not ship. `build_symbol_map` re-reads every tracked path;
this re-reads only what moved since the sha the partition already carries, so
a dispatch into a worktree pays for its diff rather than for the repository.

**Why this module reads no mtime, and must not start.** The reference engine
this ticket surveyed tests mtime plus size in its indexer and SIZE ONLY in its
reader, "because a fresh worktree checkout restamps every mtime and would
otherwise read as fully unindexed". We run many worktrees off one checkout and
cut a new one per ticket, so an mtime-based freshness check at run entry would
report a whole-repository rebuild every single time a worktree is created —
which is the failure this ticket exists to avoid, arriving by the back door.
Our oracle is git shas plus `checkout_identity`, and we read mtime NOWHERE on
either side, so the trap is structurally unreachable rather than merely
avoided — and a test greps this package for the modification-time spellings so
it stays that way. The natural optimisation a later ticket reaches for is a
`stat()` on each file and a comparison of its modification time; that is the
change that reintroduces the bug, and this paragraph is here because that
author will be standing in this file. Git's own content hashing is already the
confirm-by-hash step, and `content_hash` is reserved for
lg-code-knowledge-368.

**The three windows, and the stamp.** A refresh stamps `refresh_started_at`
and commits, analyses outside any transaction, then writes in one short
transaction that clears the stamp. SQLite serializes writers, so holding a
write transaction across the analysis pass would block every unrelated
orchestration write for as long as parsing takes. A failure in ANY of the
three windows has to clear the stamp: one nothing clears pins every later read
of that checkout at `Freshness.BUILDING` forever, while every test about a
successful refresh still passes.

`refresh_symbol_map` raises, so the failures are testable.
`refresh_at_run_entry` never does, because a stale or missing map degrades its
consumers to live grep — the designed fallback, and strictly better than
blocking a stage.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from time import perf_counter

from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.symbol_map_enums import ChangedPathsOutcome, RefreshFailure
from loregarden.models.domain.symbol_map_tables import SymbolMapPartition
from loregarden.services.symbol_map import sources, store
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.freshness import partition_for
from loregarden.services.worktree_snapshot import TreeSnapshot
from pydantic import BaseModel, ConfigDict
from sqlalchemy import update
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: What a cold build is expected to cost, in milliseconds. Exceeding it
#: changes only what the run-log line (lg-code-knowledge-2731) prints: this
#: budget never blocks, delays or fails a dispatch.
COLD_BUILD_BUDGET_MS = 10_000

#: How many mapped checkouts a cold start asks git about before giving up and
#: building. Each probe is a `merge-base` process, and this workspace has ~102
#: live worktrees; probing all of them would trade the full build this is
#: meant to avoid for a hundred processes. Ordered newest first, because the
#: partition built most recently is the likeliest ancestor of a worktree cut
#: minutes ago. Finding no ancestor is not a wrong answer — it is a full
#: build, which is exactly what happens today.
_SEED_CANDIDATE_LIMIT = 5

#: Copying an ancestor's rows, as two statements rather than as Python objects,
#: so the package keeps exactly one place that mints a file or symbol row.
#: File ids are minted fresh — reusing the ancestor's would put two partitions'
#: files under one primary key, and where the ids merely travelled into
#: `symbol_map_symbols.file_id`, retiring the ancestor would silently empty a
#: live checkout's map.
_SEED_FILES_SQL = (
    "INSERT INTO symbol_map_files "
    "(id, partition_id, path, language, extractor_tier, parse_status, parse_error, "
    "indexed_sha, index_built_at) "
    "SELECT lower(hex(randomblob(16))), ?, path, language, extractor_tier, parse_status, "
    "parse_error, indexed_sha, index_built_at "
    "FROM symbol_map_files WHERE partition_id = ?"
)
#: The symbol rows are re-pointed at the NEW file row for the same path, which
#: is why the file copy has to run first.
_SEED_SYMBOLS_SQL = (
    "INSERT INTO symbol_map_symbols "
    "(id, file_id, partition_id, name, qualified_name, kind, line, parent) "
    "SELECT lower(hex(randomblob(16))), new_file.id, ?, s.name, s.qualified_name, s.kind, "
    "s.line, s.parent "
    "FROM symbol_map_symbols s "
    "JOIN symbol_map_files old_file ON old_file.id = s.file_id "
    "JOIN symbol_map_files new_file "
    "  ON new_file.partition_id = ? AND new_file.path = old_file.path "
    "WHERE s.partition_id = ?"
)


class RefreshReport(BaseModel):
    """What one refresh actually did, including when it declined.

    Follows `GateRunResult(ok=False, …)`: the failure is a value on the result
    rather than an exception the caller must catch, and `reason` is a closed
    enum rather than an exception class name or a traceback string that no
    consumer could branch on.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    reason: RefreshFailure | None
    files_reanalyzed: int
    files_deleted: int
    files_behind: int
    elapsed_ms: int
    head_sha: str
    checkout_root: str
    #: True when this checkout had no partition at all when the refresh began.
    cold: bool
    #: True when that cold start copied an ancestor partition's rows rather
    #: than parsing the whole tree.
    seeded: bool


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _clear_refresh_stamp(session: Session, partition_id: str) -> None:
    """Clear the BUILDING stamp on a partition, from any session state.

    The rollback first is not tidiness: this runs on a failure path, the
    Session belongs to the dispatch that is still going to write run rows
    through it, and a Session left in pending-rollback raises on the next
    write — so a bookkeeping failure would become the outcome of the work it
    was only supposed to describe.
    """
    session.rollback()
    session.execute(
        update(SymbolMapPartition)
        .where(SymbolMapPartition.id == partition_id)
        .values(refresh_started_at=None)
    )
    session.commit()


def _ancestor_partition(
    session: Session, workspace_id: str, repo_root: Path, head: str
) -> SymbolMapPartition | None:
    """A partition of THIS workspace whose sha this checkout descends from.

    Scoped by workspace as well as by ancestry: `symbol_map_partitions` is
    keyed by both, and a seed that searched on the commit alone would hand one
    workspace another's map — two maps of the same files, so nothing about the
    result looks wrong until a workspace that was never built answers a query.

    A candidate that is mid-refresh is skipped. Its rows are a generation in
    progress, and the whole point of the stamp is that nothing serves one as
    complete.
    """
    identity = identify(repo_root)
    candidates = session.exec(
        select(SymbolMapPartition)
        .where(
            SymbolMapPartition.workspace_id == workspace_id,
            SymbolMapPartition.checkout_identity != identity,
            SymbolMapPartition.refresh_started_at == None,  # noqa: E711 - SQL NULL test
        )
        .order_by(SymbolMapPartition.built_at.desc())
        .limit(_SEED_CANDIDATE_LIMIT)
    ).all()
    for candidate in candidates:
        if candidate.head_sha and sources.is_ancestor(repo_root, candidate.head_sha, head):
            return candidate
    return None


def head_of(repo_root: Path, tree: TreeSnapshot | None) -> str:
    """HEAD, from the caller's snapshot when it is a reading of THIS root.

    The executor has already run one `git status --porcelain=v2 --branch`
    immediately before the refresh and nothing mutates the tree in between, so
    the sha is in the caller's hand. Asking git again is a second process on
    every dispatch for an answer already given — which is the duplication
    `worktree_snapshot` exists to remove.

    The root is compared because a snapshot is a reading of one tree; a
    snapshot of another checkout would stamp this map with a sha it never had.
    """
    if (
        tree is not None
        and tree.head_sha
        and os.path.realpath(tree.repo_root) == os.path.realpath(repo_root)
    ):
        return tree.head_sha
    return sources.head_sha(repo_root)


def seed_from_ancestor(
    session: Session, workspace_id: str, repo_root: Path, head: str
) -> SymbolMapPartition | None:
    """Give a cold checkout an ancestor's rows, or None if there is no ancestor.

    Not an optimisation in practice: `symbol_map_partitions` held zero rows in
    production against ~94 live worktrees, worktrees are cut per ticket, and a
    cold full build measured 7.9 s on this repository. Without this the common
    path is the slow path.

    The new partition carries THIS checkout's identity and root and the
    ANCESTOR's sha — never this checkout's HEAD, which would claim to describe
    a tree nothing has read. The incremental pass that follows advances it.
    """
    ancestor = _ancestor_partition(session, workspace_id, repo_root, head)
    if ancestor is None:
        return None
    seeded = SymbolMapPartition(
        workspace_id=workspace_id,
        checkout_identity=identify(repo_root),
        checkout_root=os.path.realpath(repo_root),
        head_sha=ancestor.head_sha,
        built_at=utcnow(),
    )
    session.add(seeded)
    session.flush()
    connection = session.connection()
    connection.exec_driver_sql(_SEED_FILES_SQL, (seeded.id, ancestor.id))
    connection.exec_driver_sql(_SEED_SYMBOLS_SQL, (seeded.id, seeded.id, ancestor.id))
    session.commit()
    return seeded


def _full_build(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    *,
    started: float,
    cold: bool,
) -> RefreshReport:
    report = store.build_symbol_map(session, workspace_id, repo_root)
    return RefreshReport(
        ok=True,
        reason=None,
        files_reanalyzed=report.files_seen,
        files_deleted=0,
        files_behind=0,
        elapsed_ms=_elapsed_ms(started),
        head_sha=report.head_sha,
        checkout_root=os.path.realpath(repo_root),
        cold=cold,
        seeded=False,
    )


def _declined(
    reason: RefreshFailure,
    *,
    started: float,
    repo_root: Path,
    head_sha: str = "",
) -> RefreshReport:
    return RefreshReport(
        ok=False,
        reason=reason,
        files_reanalyzed=0,
        files_deleted=0,
        files_behind=0,
        elapsed_ms=_elapsed_ms(started),
        head_sha=head_sha,
        checkout_root=os.path.realpath(repo_root),
        cold=False,
        seeded=False,
    )


def _reload(session: Session, partition_id: str) -> SymbolMapPartition:
    partition = session.get(SymbolMapPartition, partition_id)
    if partition is None:  # pragma: no cover - the row was stamped moments ago
        raise RuntimeError(f"symbol map partition {partition_id} disappeared mid-refresh")
    return partition


def _stamp_building(session: Session, partition: SymbolMapPartition) -> tuple[str, str]:
    """Mark the partition as being written, and hand back what the pass needs.

    The attributes are read back into plain values BEFORE the analysis pass,
    and the read transaction is closed behind them, because a lazy attribute
    load inside that pass would re-open a transaction and break the one
    property AC4 pins.
    """
    partition.refresh_started_at = utcnow()
    session.add(partition)
    session.commit()
    partition = _reload(session, partition.id)
    identifiers = (partition.id, partition.head_sha)
    session.commit()
    return identifiers


def refresh_symbol_map(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    tree: TreeSnapshot | None = None,
) -> RefreshReport:
    """Bring this checkout's map up to the tree it is standing on. Raises.

    The testable core: a single function that both did the work and swallowed
    every failure could not be tested for the failures it swallowed.
    """
    store.require_a_new_enough_interpreter()
    started = perf_counter()

    partition = partition_for(session, workspace_id, repo_root)
    cold = partition is None
    seeded = False
    if partition is None:
        partition = seed_from_ancestor(session, workspace_id, repo_root, head_of(repo_root, tree))
        seeded = partition is not None
        if partition is None:
            return _full_build(session, workspace_id, repo_root, started=started, cold=True)

    partition_id, base_sha = _stamp_building(session, partition)
    try:
        return _refresh_from(
            session,
            workspace_id,
            repo_root,
            tree=tree,
            partition_id=partition_id,
            base_sha=base_sha,
            started=started,
            cold=cold,
            seeded=seeded,
        )
    except BaseException:
        # Window one or two died, or the write did. Either way the stamp has
        # to go: a partition left BUILDING is a map that reads as permanently
        # untrustworthy, and nothing else in this package would ever clear it.
        _clear_refresh_stamp(session, partition_id)
        raise


def _refresh_from(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    *,
    tree: TreeSnapshot | None,
    partition_id: str,
    base_sha: str,
    started: float,
    cold: bool,
    seeded: bool,
) -> RefreshReport:
    delta = sources.changed_paths(repo_root, base_sha, tree=tree)
    if delta.outcome is ChangedPathsOutcome.GIT_FAILED:
        # The stored sha is left EXACTLY as it was. Re-stamping it here would
        # turn a recoverable staleness into a map that answers MATCHES with
        # every line number wrong, and the grep fallback would never fire.
        _clear_refresh_stamp(session, partition_id)
        return _declined(
            RefreshFailure.GIT_FAILED,
            started=started,
            repo_root=repo_root,
            head_sha=base_sha,
        )
    if delta.outcome is ChangedPathsOutcome.BASE_UNUSABLE:
        # Routine rather than exotic: a force-push, a squash-merge, a gc or a
        # copied database all leave a sha this checkout cannot resolve. A full
        # rebuild re-stamps a sha that has actually been read.
        _clear_refresh_stamp(session, partition_id)
        return _full_build(session, workspace_id, repo_root, started=started, cold=cold)

    sha = head_of(repo_root, tree)
    # Window two: every changed path is parsed with NO transaction open.
    # Reached through the module attribute rather than through a bound import,
    # so the seam stays observable and the extractor stays shared.
    extractions = [(path, store.extraction_for(repo_root, path)) for path in sorted(delta.changed)]

    # Window three: one short transaction. Even when nothing changed, the sha
    # and `built_at` advance and the stamp clears — and no file row is touched,
    # because a commit that moved HEAD without moving a source is not a reason
    # to re-mint a whole checkout's rows.
    partition = _reload(session, partition_id)
    store.apply_extractions(
        session,
        partition,
        extractions,
        deleted=delta.deleted,
        sha=sha,
        stamped_at=utcnow(),
    )
    return RefreshReport(
        ok=True,
        reason=None,
        files_reanalyzed=len(extractions),
        files_deleted=len(delta.deleted),
        #: How far behind the map was when this pass started — the number the
        #: run-log line reports, and 0 once there was nothing to do.
        files_behind=len(delta.changed | delta.deleted),
        elapsed_ms=_elapsed_ms(started),
        head_sha=sha,
        checkout_root=os.path.realpath(repo_root),
        cold=cold,
        seeded=seeded,
    )


def refresh_at_run_entry(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    tree: TreeSnapshot | None = None,
) -> RefreshReport:
    """Refresh at a dispatch, and never fail the dispatch. Raises nothing.

    The only place in this package a broad catch is allowed, and it is not
    silent: the failure is a value on the report, and it is logged ONCE at
    `warning` — `debug` and `info` sit below the default handler level, so a
    failure reported there is reported nowhere, and a per-file warning on a
    cold repository would be 3,479 lines in a run log.
    """
    started = perf_counter()
    try:
        report = refresh_symbol_map(session, workspace_id, repo_root, tree=tree)
    except sources.UnbornHeadError as exc:
        return _warned(RefreshFailure.UNBORN_HEAD, exc, started=started, repo_root=repo_root)
    except store.InterpreterTooOldError as exc:
        return _warned(
            RefreshFailure.INTERPRETER_TOO_OLD, exc, started=started, repo_root=repo_root
        )
    except Exception as exc:  # noqa: BLE001 - the degrade path; reported, never swallowed
        return _warned(RefreshFailure.UNEXPECTED, exc, started=started, repo_root=repo_root)
    if not report.ok:
        logger.warning(
            "symbol map refresh declined in %s (%s); reads degrade to live grep",
            repo_root,
            report.reason.value if report.reason else RefreshFailure.UNEXPECTED.value,
        )
    return report


def _warned(
    reason: RefreshFailure,
    exc: BaseException,
    *,
    started: float,
    repo_root: Path,
) -> RefreshReport:
    logger.warning(
        "symbol map refresh failed in %s (%s): %s; reads degrade to live grep",
        repo_root,
        reason.value,
        exc,
    )
    return _declined(reason, started=started, repo_root=repo_root)
