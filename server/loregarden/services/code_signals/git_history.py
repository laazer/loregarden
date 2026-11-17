"""The only module in this package that shells git (lg-code-knowledge-299, AC2).

One `git log --numstat` read per computation, through `run_git` (from
`services.git_subprocess`) so an ambient `GIT_DIR` from a hook cannot rebind
the read to another repository — plus one `rev-parse HEAD` for the staleness
stamp. Nothing else here spawns anything.

**Why this parser is not one of the four that already exist.**
`artifact_service._parse_numstat`, `editor_changes._numstat`,
`stage_fanout_service` and `branch_triage_service` each parse a DIFF numstat
for one range, with their own flags and no commit headers. This parses a LOG
numstat over a time window with commit headers interleaved and `-z` framing.
The named duplication of about six lines beats reshaping four unrelated
subsystems around a fifth caller.

**The framing, and why every byte of it matters.** The format prefixes each
record with a NUL-wrapped sentinel and separates header fields with NUL:

    \\0LGCS\\0<sha>\\0<author email>\\0<committer unix time>\\0<subject>\\0
    [\\n<added>\\t<deleted>\\t<path>\\0]...

A subject is attacker-controlled on any repository with contributors and may
contain every byte except NUL — a tab, the visible text `LGCS`, even the
literal `-\\t-` of a binary row. NUL is therefore the only framing a commit
message cannot forge, so the parser splits on NUL and never on visible text
or on newlines. `-z` is what keeps a path's bytes raw (a path may legally
contain a tab or a newline), and `--no-renames` is what keeps the rename row
shape — under `-z` an EMPTY path field followed by two more NUL chunks, not
the `{old => new}` the plan expected — out of the stream entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loregarden.models.domain.code_signal_enums import EmptyBasis
from loregarden.services.code_signals.config import SignalConfig
from loregarden.services.git_subprocess import run_git
from pydantic import BaseModel, ConfigDict

#: Wraps the sentinel in NULs on both sides so it cannot appear inside a
#: subject, which is the one header field an outsider writes.
RECORD_SENTINEL = b"\x00LGCS\x00"
#: Between header fields. The same byte terminates the header.
FIELD_SEP = b"\x00"

#: `%ct` is COMMITTER time: when the commit entered *this* history, which is
#: what a rolling window means on a repo that restacks on every landing.
#: `%aE` is AUTHOR email, because ownership asks who wrote the lines. The mix
#: is deliberate.
LOG_FORMAT = "%x00LGCS%x00%H%x00%aE%x00%ct%x00%s"

#: Both halves of a numstat row are `-` when git treats the path as binary —
#: including a plain text file a committed `.gitattributes` marks `-diff`.
_BINARY = b"-"


class GitSignalsReadError(RuntimeError):
    """Git exited non-zero for a reason that is not a known-empty history.

    A raise rather than an empty result, because the caller is a
    delete-and-reinsert writer: a result object it forgot to check would wipe
    a workspace's good rows and write nothing in their place.
    """

    def __init__(self, returncode: int, stderr: str) -> None:
        super().__init__(f"git exited {returncode}: {stderr.strip()}")
        self.returncode = returncode
        self.stderr = stderr


class GitSignalsParseError(ValueError):
    """A row in the log stream had a shape this parser does not recognise.

    Raised, never skipped and never guessed at: a mis-keyed path is a wrong
    answer that looks exactly like a right one.
    """


class CommitRecord(BaseModel):
    """One non-merge commit in the window, with its numstat rows."""

    model_config = ConfigDict(frozen=True)

    sha: str
    author_email: str
    #: Aware UTC. The ORM stores naive UTC; `store` converts at that boundary.
    committed_at: datetime
    subject: str
    files: list[tuple[str, int, int]] = []


@dataclass(frozen=True)
class HistoryRead:
    """What one read of a repository's history produced."""

    records: list[CommitRecord]
    head_sha: str
    empty_basis: EmptyBasis | None


#: git's own words for "this repository has no commits". The distinction has
#: to be made on stderr: `git log` exits 128 both here and on a path that is
#: not a repository at all, so the exit code alone cannot classify it.
_NO_HISTORY_MARKERS = (
    "does not have any commits yet",
    "bad default revision 'head'",
    "unknown revision or path not in the working tree",
)


def log_args(config: SignalConfig) -> list[str]:
    """The one git command this feature runs."""
    return [
        "log",
        "--no-merges",
        "--no-renames",
        "-z",
        "--numstat",
        f"--since={config.window_days} days ago",
        f"--format={LOG_FORMAT}",
    ]


def _decode_stored(raw: bytes, message: str) -> str:
    """Decode a field the package PERSISTS, strictly.

    `surrogateescape` would turn an invalid byte into a lone surrogate, which
    SQLite's driver cannot encode: the failure would then land in
    `session.commit()` inside the writer, after the delete, breaking AC9's
    property that a failed read removes no rows. Fields that are never stored
    (the subject, which only `is_fix_subject` reads) stay lenient, because a
    commit message in a legacy encoding is ordinary history and nothing
    downstream has to encode it.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitSignalsParseError(message) from error


def _parse_count(raw: bytes, row: bytes) -> int:
    if raw == _BINARY:
        return 0
    try:
        return int(raw)
    except ValueError as error:
        raise GitSignalsParseError(f"unrecognised numstat row: {row!r}") from error


def _parse_file_row(row: bytes) -> tuple[str, int, int]:
    # Bounded at two splits: a tab is the column separator AND a legal path
    # character, so an unbounded split keys `tab\there.py` on `here.py`.
    parts = row.split(b"\t", 2)
    if len(parts) != 3 or not parts[2]:
        raise GitSignalsParseError(f"unrecognised numstat row: {row!r}")
    added_raw, deleted_raw, path = parts
    if (added_raw == _BINARY) != (deleted_raw == _BINARY):
        raise GitSignalsParseError(f"unrecognised numstat row: {row!r}")
    return (
        _decode_stored(path, f"unrecognised numstat row: {row!r}"),
        _parse_count(added_raw, row),
        _parse_count(deleted_raw, row),
    )


def _parse_record(chunk: bytes) -> CommitRecord:
    fields = chunk.split(FIELD_SEP)
    # sha, email, time, subject. The header's own terminating NUL is the
    # sentinel's first byte for every record but the last, so it is consumed
    # by the record split and four fields is the complete shape of a commit
    # that touched nothing.
    if len(fields) < 4:
        raise GitSignalsParseError(f"truncated commit header: {chunk[:120]!r}")
    sha, email, when, subject = fields[:4]
    try:
        committed_at = datetime.fromtimestamp(int(when), tz=UTC)
    except (ValueError, OverflowError, OSError) as error:
        # Which of the three a clock raises for a timestamp past `time_t` is a
        # property of the platform, not of the input, so the guard is written
        # against the family (AC12a).
        raise GitSignalsParseError(f"unrecognised commit timestamp: {when!r}") from error

    rows = [row for row in fields[4:] if row]
    if rows and rows[0].startswith(b"\n"):
        # git puts exactly one newline between the header and the numstat
        # block. Everything after it is NUL-framed, so this is the only
        # newline the parser is allowed to treat as structure.
        rows[0] = rows[0][1:]

    return CommitRecord(
        sha=sha.decode("utf-8", "surrogateescape"),
        author_email=_decode_stored(email, f"unrecognised author email: {email!r}"),
        committed_at=committed_at,
        subject=subject.decode("utf-8", "surrogateescape"),
        files=[_parse_file_row(row) for row in rows if row],
    )


def parse_log_stream(stream: bytes) -> list[CommitRecord]:
    """Every record in a captured `git log` stream. No git process involved."""
    if not stream:
        return []
    chunks = stream.split(RECORD_SENTINEL)
    if chunks[0]:
        raise GitSignalsParseError(f"stream does not begin with a record: {chunks[0][:120]!r}")
    return [_parse_record(chunk) for chunk in chunks[1:]]


def read_history(repo_root: Path, config: SignalConfig) -> HistoryRead:
    """Read the window's commits out of *repo_root*, or say why there are none."""
    try:
        result = run_git(log_args(config), cwd=repo_root, check=False, capture_output=True)
    except OSError as error:
        # AC13. `repo_root` is caller-supplied, and the subprocess layer
        # resolves `cwd` BEFORE git runs, so a checkout that moved, was
        # deleted, is a regular file, is unreadable (EACCES) or is a symlink
        # loop (ELOOP) raises a raw `OSError` here with no exit code and no
        # stderr to classify. The guard is written against the family rather
        # than against the two subclasses the criterion names: which errno a
        # given platform produces for an unusable path is a property of the
        # platform. returncode 0 because git never ran — there is no exit
        # code to carry.
        raise GitSignalsReadError(0, f"repo_root is unusable: {repo_root}: {error}") from error
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace") if result.stderr else ""
        if any(marker in stderr.lower() for marker in _NO_HISTORY_MARKERS):
            return HistoryRead(records=[], head_sha="", empty_basis=EmptyBasis.NO_HISTORY)
        raise GitSignalsReadError(result.returncode, stderr)

    records = parse_log_stream(result.stdout or b"")
    head = run_git(
        ["rev-parse", "HEAD"], cwd=repo_root, check=False, capture_output=True, text=True
    )
    head_sha = head.stdout.strip() if head.returncode == 0 else ""
    basis = None if records else EmptyBasis.NO_COMMITS_IN_WINDOW
    return HistoryRead(records=records, head_sha=head_sha, empty_basis=basis)


__all__ = [
    "FIELD_SEP",
    "LOG_FORMAT",
    "RECORD_SENTINEL",
    "CommitRecord",
    "GitSignalsParseError",
    "GitSignalsReadError",
    "HistoryRead",
    "log_args",
    "parse_log_stream",
    "read_history",
]
