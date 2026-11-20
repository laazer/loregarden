"""Four pure reducers over a commit stream. No git, no Session, no DB.

Everything here is a function of the `CommitRecord` list it is handed, so the
acceptance criteria that matter most — attribution, the tie rule, the knob
boundaries — are tested against a `tmp_path` repository with no database
mounted anywhere.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

from loregarden.models.domain.code_signal_enums import EmptyBasis, PathKind
from loregarden.models.domain.provenance import Provenance
from loregarden.services.code_signals.classify import classify_path, is_fix_subject
from loregarden.services.code_signals.config import SignalConfig
from loregarden.services.code_signals.git_history import CommitRecord, read_history
from pydantic import BaseModel, ConfigDict


class FileSignals(BaseModel):
    """Everything the window says about one path. Timestamps are aware UTC."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    churn_commits: int = 0
    churn_added: int = 0
    churn_deleted: int = 0
    last_commit_at: datetime
    #: Empty exactly when nobody added a line to this path in the window — a
    #: deletion-only history, or a path git reports as binary.
    top_author: str = ""
    top_author_added_share: float = 0.0
    distinct_authors: int = 0
    bugfix_commits: int = 0
    last_bugfix_at: datetime | None = None
    #: What inferred the derived fields, resolved at READ time by `store` and
    #: left empty by the computation — the triple describes the wiring the
    #: reader resolved, and no column carries it (`code_signals.provenance`).
    provenance: tuple[Provenance, ...] = ()


class CoChangePair(BaseModel):
    """One direction of one co-change pair."""

    model_config = ConfigDict(frozen=True)

    file_path: str
    partner_path: str
    support: int


class GitSignals(BaseModel):
    """The whole result of one computation over one repository."""

    model_config = ConfigDict(frozen=True)

    files: dict[str, FileSignals] = {}
    pairs: list[CoChangePair] = []
    records: list[CommitRecord] = []
    commits_read: int = 0
    head_sha: str = ""
    #: Set only when there are no records, and only to say WHY there are none.
    #: A failed read raises instead, so this can never mean "it did not work".
    empty_basis: EmptyBasis | None = None


class _PathAccumulator:
    """Running totals for one path, before the shares are worked out."""

    __slots__ = (
        "added",
        "authors",
        "by_author",
        "bugfix_commits",
        "commits",
        "deleted",
        "last_bugfix_at",
        "last_commit_at",
    )

    def __init__(self) -> None:
        self.commits = 0
        self.added = 0
        self.deleted = 0
        self.last_commit_at: datetime | None = None
        self.by_author: dict[str, int] = defaultdict(int)
        self.authors: set[str] = set()
        self.bugfix_commits = 0
        self.last_bugfix_at: datetime | None = None


def _ownership(by_author: dict[str, int], total_added: int) -> tuple[str, float]:
    """The top author and their share, or nobody when no line was authored.

    Ties break to the lexicographically smallest email. Determinism is a
    requirement here, not a nicety: two consumers reading the same table must
    not disagree about who owns a file because a dict happened to iterate
    differently.
    """
    if total_added <= 0:
        return "", 0.0
    top = min(by_author.items(), key=lambda item: (-item[1], item[0]))
    return top[0], top[1] / total_added


def _co_change(records: list[CommitRecord], config: SignalConfig) -> list[CoChangePair]:
    """Unordered pairs seen at least `min_support` times, stored both ways.

    A commit above `max_files_per_commit` contributes no pair at all: measured
    on this repository, 62% of the pairs that survive the support floor exist
    only because of commits of 143 files and up, which are the orchestrator
    sweeping a working tree rather than two files that belong together. It
    still contributes its churn, ownership and bug-fix history — the cap drops
    pairs, never files.
    """
    support: dict[tuple[str, str], int] = defaultdict(int)
    for record in records:
        paths = sorted({path for path, _, _ in record.files})
        if not 2 <= len(paths) <= config.max_files_per_commit:
            continue
        for left, right in combinations(paths, 2):
            support[(left, right)] += 1

    pairs: list[CoChangePair] = []
    for (left, right), seen in sorted(support.items()):
        if seen < config.min_support:
            continue
        pairs.append(CoChangePair(file_path=left, partner_path=right, support=seen))
        pairs.append(CoChangePair(file_path=right, partner_path=left, support=seen))
    return pairs


def compute_signals(
    records: list[CommitRecord], config: SignalConfig
) -> tuple[dict[str, FileSignals], list[CoChangePair]]:
    """Churn, ownership, bug-fix history and co-change, over one commit stream."""
    accumulators: dict[str, _PathAccumulator] = defaultdict(_PathAccumulator)

    for record in records:
        fix = is_fix_subject(record.subject)
        for path, added, deleted in record.files:
            state = accumulators[path]
            state.commits += 1
            state.added += added
            state.deleted += deleted
            if state.last_commit_at is None or record.committed_at > state.last_commit_at:
                state.last_commit_at = record.committed_at
            state.authors.add(record.author_email)
            if added:
                state.by_author[record.author_email] += added
            # Path-level, not commit-level: a fix touching a module and its
            # doc credits the module only, and a doc-only fix credits nobody.
            if fix and classify_path(path) is PathKind.SOURCE:
                state.bugfix_commits += 1
                if state.last_bugfix_at is None or record.committed_at > state.last_bugfix_at:
                    state.last_bugfix_at = record.committed_at

    files: dict[str, FileSignals] = {}
    for path in sorted(accumulators):
        state = accumulators[path]
        top_author, share = _ownership(state.by_author, state.added)
        files[path] = FileSignals(
            file_path=path,
            churn_commits=state.commits,
            churn_added=state.added,
            churn_deleted=state.deleted,
            last_commit_at=state.last_commit_at,
            top_author=top_author,
            top_author_added_share=share,
            distinct_authors=len(state.authors),
            bugfix_commits=state.bugfix_commits,
            last_bugfix_at=state.last_bugfix_at,
        )

    return files, _co_change(records, config)


def compute_git_signals(repo_root: Path, config: SignalConfig) -> GitSignals:
    """Every signal for *repo_root* over *config*'s window.

    Takes no session and imports no database. Raises `GitSignalsReadError`
    when git failed and `GitSignalsParseError` when its output had a shape
    this parser does not recognise; an empty window is a value carrying an
    `empty_basis`, never an exception and never silence.
    """
    history = read_history(repo_root, config)
    files, pairs = compute_signals(history.records, config)
    return GitSignals(
        files=files,
        pairs=pairs,
        records=history.records,
        commits_read=len(history.records),
        head_sha=history.head_sha,
        empty_basis=history.empty_basis,
    )


__all__ = [
    "CoChangePair",
    "FileSignals",
    "GitSignals",
    "compute_git_signals",
    "compute_signals",
]
