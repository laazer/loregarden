"""A run's log is appended, not rewritten in full on every flush.

`lg-workflow-integrity-687`. The live log was one artifact row whose
`content_json` held every line, rewritten on each append. The largest such row
in the live database was 320,619 characters, and a 78-minute run rewrote it
roughly 11,700 times. One of those writes lost a "database is locked" race and
took the whole orchestration with it, discarding a completed stage whose work
survived only because the agent had committed to git first.

The sizing argument these tests pin: what matters is the size of a single
write, not the total. A longer run must not make each write bigger.
"""

from __future__ import annotations

import json
import threading

from loregarden.models.domain import Artifact, RunLogLine, RunStatus
from loregarden.services.artifact_service import load_run_log
from loregarden.services.log_storage import LOG_STORAGE_ROWS, read_log_lines
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.seed import seed_database
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_workspace_ticket


def _streamer(session: Session) -> RunLogStreamer:
    """A streamer over a run whose ticket and workspace really exist.

    Built in a helper rather than inline because `artifacts.ticket_id` is NOT
    NULL: a run created without a ticket fails at INSERT, which looks exactly
    like the code under test being broken.
    """
    seed_database(session)
    ticket = make_workspace_ticket(session, "log-append-1")
    run = make_agent_run(
        session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="RUN-LOG",
    )
    return RunLogStreamer(
        run_id=run.id,
        ticket_id=run.ticket_id,
        run_code=run.run_code,
        agent_id=run.agent_id,
        skill_name="",
    )


def test_the_artifact_row_no_longer_carries_the_lines(db_session):
    """AC1. The bulk moved off `artifacts.content_json`; what stays is the small
    live tail and a marker saying where the lines went."""
    streamer = _streamer(db_session)
    for i in range(50):
        streamer.append("OUT", f"line {i}", force=True)

    artifact = db_session.exec(
        select(Artifact).where(Artifact.run_id == streamer.run_id, Artifact.kind == "log")
    ).first()
    body = json.loads(artifact.content_json)
    assert body["storage"] == LOG_STORAGE_ROWS
    assert "lines" not in body or not body.get("lines")

    rows = db_session.exec(select(RunLogLine).where(RunLogLine.run_id == streamer.run_id)).all()
    assert len(rows) == 50


def test_a_flush_writes_only_what_is_new(db_session):
    """AC1, stated as the property that actually matters. The second flush must
    not re-write the first flush's lines."""
    streamer = _streamer(db_session)
    streamer.append("OUT", "first", force=True)
    after_first = db_session.exec(
        select(RunLogLine).where(RunLogLine.run_id == streamer.run_id)
    ).all()
    first_ids = {row.id for row in after_first}

    streamer.append("OUT", "second", force=True)
    after_second = db_session.exec(
        select(RunLogLine).where(RunLogLine.run_id == streamer.run_id)
    ).all()

    assert len(after_second) == 2
    # The original rows are untouched — same ids, not replacements.
    assert first_ids < {row.id for row in after_second}


def test_write_size_does_not_grow_with_the_log(db_session):
    """AC1's measurement, against the 320KB observed live.

    The old shape rewrote the whole log every flush, so write number 1000 cost a
    thousand lines. Here each flush costs one line no matter how long the run has
    been going — this is the assertion that would have failed before the change.
    """
    streamer = _streamer(db_session)
    line = "x" * 500

    streamer.append("OUT", line, force=True)
    early = streamer._last_persist_bytes

    for _ in range(300):
        streamer.append("OUT", line, force=True)
    late = streamer._last_persist_bytes

    assert late < 2 * early, (
        f"a late write cost {late}B against an early {early}B — "
        "write size is still tracking run length"
    )
    assert late < 4000, f"a single flush wrote {late}B for one 500B line"


def test_a_reader_sees_the_lines_through_the_artifact(db_session):
    """The lines moved, so every reader must still assemble the same body."""
    streamer = _streamer(db_session)
    streamer.append("OUT", "hello", force=True)
    streamer.append("ERR", "trouble", force=True)

    body = load_run_log(db_session, streamer.run_id)
    assert [line["text"] for line in body["lines"]] == ["hello", "trouble"]
    assert [line["tag"] for line in body["lines"]] == ["OUT", "ERR"]


def test_a_log_written_before_the_change_still_renders(db_session):
    """No backfill was run, deliberately — rewriting every historical log row is
    the write this ticket exists to stop. Old artifacts carry no marker and keep
    their inline lines, so they must still read back."""
    streamer = _streamer(db_session)
    legacy = Artifact(
        ticket_id=streamer.ticket_id,
        run_id=streamer.run_id,
        kind="log",
        title="Run OLD",
        content_json=json.dumps(
            {"lines": [{"time": "00:00:01", "tag": "OUT", "text": "from before"}], "live": None}
        ),
    )
    db_session.add(legacy)
    db_session.commit()

    body = json.loads(legacy.content_json)
    assert [line["text"] for line in read_log_lines(db_session, streamer.run_id, body)] == [
        "from before"
    ]


def test_reattaching_appends_rather_than_duplicating(db_session):
    """A second streamer over the same run resumes the sequence. Getting this
    wrong would double every line on reattach, which the old full-rewrite shape
    could not do."""
    streamer = _streamer(db_session)
    streamer.append("OUT", "before", force=True)

    resumed = RunLogStreamer(
        run_id=streamer.run_id,
        ticket_id=streamer.ticket_id,
        run_code=streamer.run_code,
        agent_id=streamer.agent_id,
        skill_name="",
    )
    resumed._hydrate()
    resumed.append("OUT", "after", force=True)

    rows = db_session.exec(
        select(RunLogLine).where(RunLogLine.run_id == streamer.run_id).order_by(RunLogLine.seq)
    ).all()
    assert [row.text for row in rows] == ["before", "after"]
    assert [row.seq for row in rows] == [1, 2]


def test_concurrent_writers_do_not_lose_lines(db_session):
    """AC5. Several agent sessions work in sibling worktrees against one SQLite
    file here, so contention is the normal condition rather than an edge case.
    Every line each writer appends must survive."""
    streamer = _streamer(db_session)
    run_id, ticket_id = streamer.run_id, streamer.ticket_id

    writers = 4
    per_writer = 15
    errors: list[BaseException] = []

    def write(index: int) -> None:
        try:
            own = RunLogStreamer(
                run_id=run_id,
                ticket_id=ticket_id,
                run_code="RUN-LOG",
                agent_id="backend_implementer",
                skill_name="",
            )
            own._hydrate()
            for n in range(per_writer):
                own.append("OUT", f"w{index}-{n}", force=True)
        except BaseException as exc:  # surfaced below; a swallowed one would pass
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"a concurrent writer failed: {errors[0]!r}"

    with Session(db_session.get_bind()) as fresh:
        rows = fresh.exec(select(RunLogLine).where(RunLogLine.run_id == run_id)).all()
    texts = {row.text for row in rows}
    for i in range(writers):
        for n in range(per_writer):
            assert f"w{i}-{n}" in texts, f"writer {i} lost line {n}"


def test_finalize_still_clears_the_live_tail(db_session):
    """The live tail is the one thing still held in the artifact row; a finished
    run must not keep showing it."""
    streamer = _streamer(db_session)
    streamer.append("OUT", "working", force=True)
    streamer.finalize(status=RunStatus.SUCCEEDED, stderr="")

    artifact = db_session.exec(
        select(Artifact).where(Artifact.run_id == streamer.run_id, Artifact.kind == "log")
    ).first()
    assert json.loads(artifact.content_json).get("live") in (None, "")


def test_the_artifact_write_is_bounded_by_the_live_tail_not_the_run(db_session):
    """AC1's ceiling, stated honestly.

    The artifact row still carries the live tail, so a write is not free — it is
    bounded by `MAX_LIVE_CHARS` (64,000). The property that matters is that the
    bound is a constant rather than a function of how long the run has been
    going: the 320,619-char row observed live grew with the run, and this does
    not. Measured over 400 lines the artifact write stays a few dozen bytes.
    """
    streamer = _streamer(db_session)
    biggest = 0
    for i in range(400):
        streamer.append("OUT", f"{i} " + "x" * 300, force=True)
        artifact = db_session.exec(
            select(Artifact).where(Artifact.run_id == streamer.run_id, Artifact.kind == "log")
        ).first()
        db_session.refresh(artifact)
        biggest = max(biggest, len(artifact.content_json))

    assert biggest <= RunLogStreamer.MAX_LIVE_CHARS + 200, (
        f"artifact write reached {biggest} chars, above the live-tail bound"
    )
    assert biggest < 5000, (
        f"artifact write grew to {biggest} chars over 400 lines — it is still tracking run length"
    )
