"""`RunLogStreamer.tail_offset` — where a restarted server resumes (spec S5, AC9).

The byte offset into `<stem>.out` is the only thing that makes reattachment
resume rather than replay. It has to be written in the SAME transaction as the
`RunLogLine` rows it corresponds to: an offset ahead of the rows loses every
line in between, and one behind them re-ingests lines the operator already has.
Risk #4 in the spec, and it only shows up at a restart boundary.

A byte offset rather than `_persisted_seq`, because one raw stream line maps to
0..N formatted `LogLine`s — a sequence number cannot be converted back into a
file position.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.models.domain import Artifact, RunLogLine, RunStatus, Ticket
from loregarden.services import run_log_stream
from loregarden.services.run_log_stream import RunLogStreamer
from loregarden.services.seed import seed_database
from sqlmodel import Session, select
from tests.factories import make_agent_run

RUN_ID = "run_offset_1"


@pytest.fixture(name="ticket_id")
def ticket_id_fixture(isolated_db) -> str:
    """The seeded ticket's id, and a run row for the streamer to write against.

    A plain string rather than the ORM object: the `make_agent_run` commit
    expires the instance, and a detached one raises on its own `id` — which,
    inside a fixture, reads as a broken feature rather than a broken fixture.
    """
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(select(Ticket).limit(1)).first()
        assert ticket
        ticket_id, workspace_id = ticket.id, ticket.workspace_id
        make_agent_run(
            session,
            run_id=RUN_ID,
            run_code="run_off01",
            ticket_id=ticket_id,
            workspace_id=workspace_id,
        )
        return ticket_id


def _streamer(ticket_id: str) -> RunLogStreamer:
    return RunLogStreamer(
        run_id=RUN_ID,
        ticket_id=ticket_id,
        run_code="run_off01",
        agent_id="backend_implementer",
        skill_name="",
    )


def _stored_content(isolated_db) -> dict:
    with Session(isolated_db) as session:
        artifact = session.exec(
            select(Artifact).where(Artifact.run_id == RUN_ID, Artifact.kind == "log")
        ).first()
        assert artifact is not None
        return json.loads(artifact.content_json or "{}")


def _row_count(isolated_db) -> int:
    with Session(isolated_db) as session:
        return len(session.exec(select(RunLogLine).where(RunLogLine.run_id == RUN_ID)).all())


def test_a_new_streamer_starts_at_offset_zero(ticket_id):
    assert _streamer(ticket_id).tail_offset == 0


def test_the_offset_is_written_into_the_log_artifacts_content(ticket_id, isolated_db):
    """AC9. Under the key `tail_offset`, beside `live` and `storage`."""
    streamer = _streamer(ticket_id)
    streamer.start("claude -p 'go'")
    streamer.append_stream_line("first line of output")
    streamer.tail_offset = 21
    streamer.touch()

    assert _stored_content(isolated_db)["tail_offset"] == 21


def test_a_fresh_streamer_hydrates_the_stored_offset(ticket_id, isolated_db):
    """AC9's real use: the process that resumes the run is not the one that
    started it, and reads the offset off the row."""
    first = _streamer(ticket_id)
    first.start("claude -p 'go'")
    first.append_stream_line("alpha")
    first.tail_offset = 6
    first.touch()

    second = _streamer(ticket_id)
    second._hydrate()

    assert second.tail_offset == 6


def test_a_log_artifact_written_before_this_change_hydrates_to_zero(ticket_id, isolated_db):
    """AC9. 2,285 runs predate the key.

    Zero here means "nothing to tail" — no output file exists for such a run —
    rather than "re-ingest this file from byte 0", which is why the reattach
    path checks for the file at all.
    """
    streamer = _streamer(ticket_id)
    streamer.start("claude -p 'go'")
    with Session(isolated_db) as session:
        artifact = session.exec(
            select(Artifact).where(Artifact.run_id == RUN_ID, Artifact.kind == "log")
        ).first()
        assert artifact is not None
        content = json.loads(artifact.content_json or "{}")
        content.pop("tail_offset", None)
        artifact.content_json = json.dumps(content)
        session.add(artifact)
        session.commit()

    second = _streamer(ticket_id)
    second._hydrate()

    assert second.tail_offset == 0


def test_a_null_offset_hydrates_to_zero_rather_than_raising(ticket_id, isolated_db):
    """`live` is stored as None when empty; the same shape must be survivable here."""
    streamer = _streamer(ticket_id)
    streamer.start("claude -p 'go'")
    with Session(isolated_db) as session:
        artifact = session.exec(
            select(Artifact).where(Artifact.run_id == RUN_ID, Artifact.kind == "log")
        ).first()
        assert artifact is not None
        content = json.loads(artifact.content_json or "{}")
        content["tail_offset"] = None
        artifact.content_json = json.dumps(content)
        session.add(artifact)
        session.commit()

    second = _streamer(ticket_id)
    second._hydrate()

    assert second.tail_offset == 0


def test_the_offset_advances_with_the_rows_it_corresponds_to(ticket_id, isolated_db):
    """AC9's pairing invariant, asserted at three points mid-stream.

    This is what "killed mid-stream" means for a durable store: whatever the
    streamer last committed, the offset it committed with must be exactly past
    the last line that landed.
    """
    streamer = _streamer(ticket_id)
    streamer.start("claude -p 'go'")
    source_lines = ["one", "two", "three"]
    consumed = 0

    for index, line in enumerate(source_lines, start=1):
        streamer.append_stream_line(line)
        consumed += len(line.encode("utf-8")) + 1
        streamer.tail_offset = consumed
        streamer.touch()

        hydrated = _streamer(ticket_id)
        hydrated._hydrate()
        assert hydrated.tail_offset == consumed
        assert sum(1 for stored in hydrated._lines if stored["text"] in source_lines) == index


def test_a_failed_persist_advances_neither_the_rows_nor_the_offset(ticket_id, isolated_db):
    """AC9/risk #4. The offset must live inside `_persist`'s own transaction.

    Written outside it, a write that loses a "database is locked" race leaves
    an offset ahead of the rows — and every line between them is lost silently
    at the next restart, which is the exact shape of lg-workflow-integrity-687.
    """
    streamer = _streamer(ticket_id)
    streamer.start("claude -p 'go'")
    # The offset is set BEFORE the append, which is the ordering the owner uses:
    # `append_stream_line` can persist by itself, and the offset written in that
    # transaction has to be the one just past the row it goes with. Setting it
    # afterwards is the lag this module exists to rule out, and a test written
    # that way asserts the defect.
    streamer.tail_offset = 8
    streamer.append_stream_line("durable")
    rows_before = _row_count(isolated_db)
    assert _stored_content(isolated_db)["tail_offset"] == 8

    streamer.tail_offset = 34
    with (
        mock.patch.object(
            run_log_stream, "sqlite_write_retry", side_effect=RuntimeError("database is locked")
        ),
        pytest.raises(RuntimeError),
    ):
        streamer.touch()

    assert _row_count(isolated_db) == rows_before
    assert _stored_content(isolated_db)["tail_offset"] == 8, (
        "the offset moved without the rows it points past"
    )


def test_finalizing_keeps_the_offset_on_the_row(ticket_id, isolated_db):
    """A settled run's offset is still the record of how much of its file was
    read — the orphan sweep and any post-mortem read it."""
    streamer = _streamer(ticket_id)
    streamer.start("claude -p 'go'")
    streamer.append_stream_line("work")
    streamer.tail_offset = 5
    streamer.finalize(status=RunStatus.SUCCEEDED)

    assert _stored_content(isolated_db)["tail_offset"] == 5
