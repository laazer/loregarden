"""`loregarden merge-queue wait` — block until a merge-queue entry lands or stops.

    loregarden merge-queue wait 680da8b8-e5f6-4c94-a277-77b2e9a7667c

Prints each change of state to stderr as it happens and the outcome to stdout.
Exit 0 when it merged; 1 when it failed, timed out or was withdrawn (the reason
is printed); 2 for an entry id the queue does not have; `EXIT_WAIT_STOPPED`
(75) when the wait ended with no verdict, because its deadline passed or the
server that runs the queue stopped answering; `EXIT_WAIT_UNREADABLE` (78) when
this build cannot read the queue from the database.

It only reads, so it opens the database without migrating it: run from a
worktree whose branch adds a migration main does not ship yet, it still waits. Run it in the background and read its exit code, instead of writing
a polling loop. See `services/merge_queue_wait.py`.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from loregarden.cli.errors import EXIT_ERROR, UsageError
from loregarden.db import session as db_session
from loregarden.services.merge_queue import QueueNotFound
from loregarden.services.merge_queue_requests import EntryDetail, entry_detail
from loregarden.services.merge_queue_wait import (
    EntryDidNotMerge,
    WaitPolicy,
    WaitStopped,
    wait_for_entry,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

#: No verdict: still live at the deadline, or no server moving the queue.
#: EX_TEMPFAIL, as `loregarden capacity run` uses it for a stalled line.
EXIT_WAIT_STOPPED = 75

#: No verdict because this build cannot read the queue: no database, one never
#: migrated, or a query over a schema this build disagrees with. EX_CONFIG: a
#: retry from the same checkout fails the same way. Kept off 1, which a caller
#: reads as "the entry stopped".
EXIT_WAIT_UNREADABLE = 78

DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


def _report(line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{stamp}] {line}", file=sys.stderr, flush=True)


def _server_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=5) as response:  # noqa: S310 - operator-set local URL
            return response.status == 200
    except (
        urllib.error.URLError,
        OSError,
    ):  # silent-ok: an unreachable server is the answer; wait_for_entry reports it
        return False


def _read(entry_id: str) -> EntryDetail:
    # The recorded checks, not GitHub's: the loop refreshes them each cycle, and
    # a wait of hours should not cost a GitHub read every poll.
    with Session(db_session.engine) as session:
        return entry_detail(session, entry_id, live=False)


def _wait(args: argparse.Namespace) -> str:
    try:
        return _wait_readable(args)
    except (db_session.DatabaseNotReadable, SQLAlchemyError) as unreadable:
        print(f"cannot read the merge queue: {unreadable}", file=sys.stderr)
        raise SystemExit(EXIT_WAIT_UNREADABLE) from unreadable


def _wait_readable(args: argparse.Namespace) -> str:
    pending = db_session.open_db_for_read()
    if pending:
        _report(
            f"reading without migrating: the database has not applied {len(pending)} "
            f"migration(s) this build carries ({', '.join(pending[:3])}"
            + (", …" if len(pending) > 3 else "")
            + ")"
        )
    url = args.server_url.rstrip("/")
    policy = WaitPolicy(
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_minutes * 60,
        unreachable_seconds=args.unreachable_minutes * 60,
    )
    try:
        entry = wait_for_entry(
            lambda: _read(args.entry_id), lambda: _server_up(url), _report, policy
        )
    except QueueNotFound as missing:
        raise UsageError(str(missing)) from missing
    except EntryDidNotMerge as stopped:
        print(f"did not merge: {stopped}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from stopped
    except WaitStopped as stopped:
        print(f"stopped waiting: {stopped}", file=sys.stderr)
        raise SystemExit(EXIT_WAIT_STOPPED) from stopped
    return f"merged | {entry.detail}"


def register(commands: argparse._SubParsersAction) -> None:
    wait = commands.add_parser(
        "wait",
        help="Block until a merge-queue entry merges (exit 0) or stops (exit 1).",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    wait.add_argument("entry_id", help="The entry id that loregarden_enqueue_merge returned.")
    wait.add_argument("--poll-seconds", type=float, default=30.0)
    wait.add_argument("--timeout-minutes", type=float, default=300.0)
    wait.add_argument(
        "--unreachable-minutes",
        type=float,
        default=10.0,
        help="Stop once the queue's server has not answered /health for this long.",
    )
    wait.add_argument(
        "--server-url",
        default=os.environ.get("LOREGARDEN_API_URL", DEFAULT_SERVER_URL),
        help="The server that runs the queue (default $LOREGARDEN_API_URL or %(default)s).",
    )
    wait.set_defaults(run=_wait)
