"""The only database writer for the behavioural signals, plus the two readers.

A refresh is a FULL recompute: it reads the repository first, then deletes
this workspace's rows from both tables and reinserts, in one transaction.
Reading first is what makes a failed read safe — `compute_git_signals` raises
before a single row is deleted, so a mistyped repository path cannot cost a
workspace its good rows.

Nothing here shells git. `head_sha` arrives on the computed result, because
`git_history` is the only module in the package allowed to spawn a process.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from loregarden.models.domain.code_signal_enums import EmptyBasis, SignalKind
from loregarden.models.domain.code_signal_tables import FileCoChangePair, FileGitSignal
from loregarden.models.domain.enums import utcnow
from loregarden.services.code_signals.compute import FileSignals, compute_git_signals
from loregarden.services.code_signals.config import SignalConfig
from loregarden.services.code_signals.provenance import resolved_provenance
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete
from sqlmodel import Session, select

#: Which column each ranked signal reads. Keeping the mapping here, keyed by
#: the enum, is what stops a caller passing a column name as a string.
_RANKED_COLUMN = {
    SignalKind.CHURN_COMMITS: FileGitSignal.churn_commits,
    SignalKind.BUGFIX_COMMITS: FileGitSignal.bugfix_commits,
}


class RefreshReport(BaseModel):
    """What one refresh actually did.

    A return value rather than a log line: a count nobody reads cannot tell a
    working computation from a silent no-op, and this is the only measurement
    the feature has (AC10).
    """

    model_config = ConfigDict(frozen=True)

    commits_read: int
    files_seen: int
    signal_rows: int
    pair_rows: int
    elapsed_seconds: float
    head_sha: str
    empty_basis: EmptyBasis | None = None


def _naive_utc(moment: datetime | None) -> datetime | None:
    """Aware UTC from the computation to the naive UTC the ORM stores."""
    return None if moment is None else moment.replace(tzinfo=None)


def _aware_utc(moment: datetime | None) -> datetime | None:
    """Naive UTC out of the ORM back to the aware UTC callers compare against."""
    return None if moment is None else moment.replace(tzinfo=UTC)


def _to_value(row: FileGitSignal) -> FileSignals:
    """The stored row back as the value object the computation produced.

    The provenance triple is resolved here, at the read, because nothing stores
    it: the row carries measurements, and what inferred the derived ones is a
    fact about this build's wiring rather than about the row.
    """
    return FileSignals(
        provenance=resolved_provenance(),
        file_path=row.file_path,
        churn_commits=row.churn_commits,
        churn_added=row.churn_added,
        churn_deleted=row.churn_deleted,
        last_commit_at=_aware_utc(row.last_commit_at),
        top_author=row.top_author,
        top_author_added_share=row.top_author_added_share,
        distinct_authors=row.distinct_authors,
        bugfix_commits=row.bugfix_commits,
        last_bugfix_at=_aware_utc(row.last_bugfix_at),
    )


def refresh_workspace_signals(
    session: Session,
    workspace_id: str,
    repo_root: Path,
    config: SignalConfig,
) -> RefreshReport:
    """Recompute *workspace_id*'s signals from *repo_root* and replace its rows."""
    started = perf_counter()
    signals = compute_git_signals(repo_root, config)
    stamped_at = utcnow()

    session.execute(delete(FileGitSignal).where(FileGitSignal.workspace_id == workspace_id))
    session.execute(delete(FileCoChangePair).where(FileCoChangePair.workspace_id == workspace_id))

    session.add_all(
        FileGitSignal(
            workspace_id=workspace_id,
            file_path=value.file_path,
            churn_commits=value.churn_commits,
            churn_added=value.churn_added,
            churn_deleted=value.churn_deleted,
            last_commit_at=_naive_utc(value.last_commit_at),
            top_author=value.top_author,
            top_author_added_share=value.top_author_added_share,
            distinct_authors=value.distinct_authors,
            bugfix_commits=value.bugfix_commits,
            last_bugfix_at=_naive_utc(value.last_bugfix_at),
            window_days=config.window_days,
            head_sha=signals.head_sha,
            computed_at=stamped_at,
        )
        for value in signals.files.values()
    )
    session.add_all(
        FileCoChangePair(
            workspace_id=workspace_id,
            file_path=pair.file_path,
            partner_path=pair.partner_path,
            support=pair.support,
            computed_at=stamped_at,
        )
        for pair in signals.pairs
    )
    session.commit()

    return RefreshReport(
        commits_read=signals.commits_read,
        files_seen=len(signals.files),
        signal_rows=len(signals.files),
        pair_rows=len(signals.pairs),
        elapsed_seconds=perf_counter() - started,
        head_sha=signals.head_sha,
        empty_basis=signals.empty_basis,
    )


def signals_for_path(session: Session, workspace_id: str, path: str) -> FileSignals | None:
    """One path's signals, or None when the window never saw it.

    Scoped to the workspace: the unique index is `(workspace_id, file_path)`,
    and a reader that queried the path alone would return whichever
    workspace's row the planner reached first.
    """
    row = session.exec(
        select(FileGitSignal).where(
            FileGitSignal.workspace_id == workspace_id,
            FileGitSignal.file_path == path,
        )
    ).first()
    return None if row is None else _to_value(row)


def top_files_by(
    session: Session, workspace_id: str, signal: SignalKind, limit: int
) -> list[FileSignals]:
    """The *limit* highest-scoring paths for *signal*, highest first.

    `file_path` is the secondary sort so a tie has one answer rather than
    whichever the planner reaches first — AC1 asks for determinism, and two
    files with equal churn is the ordinary case, not the corner. A limit of
    zero is a request for nothing, and must not reach SQL, where a negative
    LIMIT means unlimited.
    """
    if limit <= 0:
        return []
    column = _RANKED_COLUMN[signal]
    rows = session.exec(
        select(FileGitSignal)
        .where(FileGitSignal.workspace_id == workspace_id)
        .order_by(column.desc(), FileGitSignal.file_path.asc())
        .limit(limit)
    ).all()
    return [_to_value(row) for row in rows]


__all__ = ["RefreshReport", "refresh_workspace_signals", "signals_for_path", "top_files_by"]
