import json

from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from loregarden.models.domain import Artifact, RunStatus, Ticket
from loregarden.services.run_log_stream import RunLogStreamer, format_stream_payload
from loregarden.services.seed import seed_database


def test_format_stream_payload_assistant_text():
    payload = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Planning the implementation"}]},
    }
    assert format_stream_payload(payload) == ("OUT", "Planning the implementation")


def test_run_log_streamer_updates_cmd_after_bootstrap():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import loregarden.services.run_log_stream as stream_mod

    original_engine = stream_mod.engine
    stream_mod.engine = engine
    try:
        with Session(engine) as session:
            seed_database(session)
            ticket = session.exec(select(Ticket).limit(1)).first()
            assert ticket

            bootstrap = RunLogStreamer(
                run_id="run_cmd",
                ticket_id=ticket.id,
                run_code="run_cmd",
                agent_id="static_qa",
                skill_name="run_tests",
            )
            bootstrap.start("Queuing agent…")

            executor = RunLogStreamer(
                run_id="run_cmd",
                ticket_id=ticket.id,
                run_code="run_cmd",
                agent_id="static_qa",
                skill_name="run_tests",
            )
            executor.start("claude -p execute tests")

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_cmd", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            cmd_lines = [line for line in content["lines"] if line["tag"] == "CMD"]
            assert cmd_lines
            assert cmd_lines[-1]["text"] == "claude -p execute tests"
    finally:
        stream_mod.engine = original_engine


def test_run_log_streamer_accumulates_stream_deltas():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import loregarden.services.run_log_stream as stream_mod

    original_engine = stream_mod.engine
    stream_mod.engine = engine
    try:
        with Session(engine) as session:
            seed_database(session)
            ticket = session.exec(select(Ticket).limit(1)).first()
            assert ticket

            streamer = RunLogStreamer(
                run_id="run_stream",
                ticket_id=ticket.id,
                run_code="run_stream",
                agent_id="static_qa",
                skill_name="run_tests",
            )
            streamer.append_stream_line(
                json.dumps({"type": "content_block_delta", "delta": {"text": "Hello "}})
            )
            streamer.append_stream_line(
                json.dumps({"type": "content_block_delta", "delta": {"text": "world"}})
            )
            streamer.append_stream_line(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Hello world"}]},
                    }
                )
            )

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_stream", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            out_lines = [line for line in content["lines"] if line["tag"] == "OUT"]
            assert out_lines
            assert out_lines[-1]["text"] == "Hello world"
            assert content["live"] == "Hello world"
    finally:
        stream_mod.engine = original_engine


def test_run_log_streamer_persists_live_log():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import loregarden.services.run_log_stream as stream_mod

    original_engine = stream_mod.engine
    stream_mod.engine = engine
    try:
        with Session(engine) as session:
            seed_database(session)
            ticket = session.exec(select(Ticket).limit(1)).first()
            assert ticket

            streamer = RunLogStreamer(
                run_id="run_test",
                ticket_id=ticket.id,
                run_code="run_test",
                agent_id="planner",
                skill_name="plan",
            )
            streamer.start("claude -p hello")
            streamer.append("OUT", "first line", force=True)
            streamer.set_live("thinking…")

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_test", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            assert content["live"] == "thinking…"
            assert any(line["text"] == "first line" for line in content["lines"])
    finally:
        stream_mod.engine = original_engine


def test_run_log_streamer_keeps_buffer_on_non_json_line():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import loregarden.services.run_log_stream as stream_mod

    original_engine = stream_mod.engine
    stream_mod.engine = engine
    try:
        with Session(engine) as session:
            seed_database(session)
            ticket = session.exec(select(Ticket).limit(1)).first()
            assert ticket

            streamer = RunLogStreamer(
                run_id="run_mixed",
                ticket_id=ticket.id,
                run_code="run_mixed",
                agent_id="static_qa",
                skill_name="run_tests",
            )
            streamer.append_stream_line(
                json.dumps({"type": "content_block_delta", "delta": {"text": "Hello world. "}})
            )
            streamer.append_stream_line("plain stderr note")

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_mixed", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            out_lines = [line for line in content["lines"] if line["tag"] == "OUT"]
            assert out_lines
            assert out_lines[0]["text"] == "Hello world."
            assert out_lines[-1]["text"] == "plain stderr note"
    finally:
        stream_mod.engine = original_engine


def test_run_log_streamer_assistant_does_not_truncate_delta_buffer():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import loregarden.services.run_log_stream as stream_mod

    original_engine = stream_mod.engine
    stream_mod.engine = engine
    try:
        with Session(engine) as session:
            seed_database(session)
            ticket = session.exec(select(Ticket).limit(1)).first()
            assert ticket

            long_text = "### Verified\n- item one\n- item two\n- item three"
            streamer = RunLogStreamer(
                run_id="run_assistant",
                ticket_id=ticket.id,
                run_code="run_assistant",
                agent_id="static_qa",
                skill_name="run_tests",
            )
            for chunk in ["### Verified\n", "- item one\n", "- item two\n", "- item three"]:
                streamer.append_stream_line(
                    json.dumps({"type": "content_block_delta", "delta": {"text": chunk}})
                )
            streamer.append_stream_line(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "### Verified"}]},
                    }
                )
            )

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_assistant", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            out_lines = [line for line in content["lines"] if line["tag"] == "OUT"]
            assert out_lines
            assert out_lines[-1]["text"] == long_text
    finally:
        stream_mod.engine = original_engine


def test_run_log_streamer_persists_long_output():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import loregarden.services.run_log_stream as stream_mod

    original_engine = stream_mod.engine
    stream_mod.engine = engine
    try:
        with Session(engine) as session:
            seed_database(session)
            ticket = session.exec(select(Ticket).limit(1)).first()
            assert ticket

            long_text = "x" * 5000
            streamer = RunLogStreamer(
                run_id="run_long",
                ticket_id=ticket.id,
                run_code="run_long",
                agent_id="static_qa",
                skill_name="run_tests",
            )
            streamer.append_stream_line(
                json.dumps({"type": "content_block_delta", "delta": {"text": long_text}})
            )
            streamer.finalize(status=RunStatus.SUCCEEDED)

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_long", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            out_text = "".join(line["text"] for line in content["lines"] if line["tag"] == "OUT")
            assert out_text == long_text
    finally:
        stream_mod.engine = original_engine
