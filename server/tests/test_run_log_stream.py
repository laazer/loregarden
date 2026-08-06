import json

from loregarden.models.domain import Artifact, RunStatus, Ticket
from loregarden.services.run_log_stream import RunLogStreamer, format_stream_payload
from loregarden.services.seed import seed_database
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def test_format_stream_payload_assistant_text():
    payload = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Planning the implementation"}]},
    }
    assert format_stream_payload(payload) == ("OUT", "Planning the implementation")


def test_format_stream_payload_codex_json_events():
    assert format_stream_payload({"type": "thread.started", "thread_id": "abc"}) == (
        "SYS",
        "codex thread · abc",
    )
    assert format_stream_payload(
        {
            "type": "item.started",
            "item": {"type": "command_execution", "command": "pytest -q"},
        }
    ) == ("TOOL", "$ pytest -q")
    assert format_stream_payload(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Done."},
        }
    ) == ("OUT", "Done.")
    assert format_stream_payload(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }
    ) == ("SYS", "codex turn done · in=10 out=4")
    assert format_stream_payload(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "ls",
                "status": "completed",
                "aggregated_output": "a\nb\n",
            },
        }
    ) == ("TOOL", "$ ls · completed\na\nb")


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


def test_run_log_streamer_coalesces_cursor_partial_assistant_tokens():
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

            first = (
                "I need to take a closer look at the code before deciding how "
                "startup recovery should work."
            )
            remainder = " Then I will inspect the tests."
            complete = first + remainder
            streamer = RunLogStreamer(
                run_id="run_cursor_partial",
                ticket_id=ticket.id,
                run_code="run_cursor_partial",
                agent_id="planner",
                skill_name="plan",
                partial_output=True,
            )
            for index, token in enumerate(complete.split(" ")):
                prefix = "" if index == 0 else " "
                streamer.append_stream_line(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {"content": [{"type": "text", "text": f"{prefix}{token}"}]},
                        }
                    )
                )

            artifact = session.exec(
                select(Artifact).where(
                    Artifact.run_id == "run_cursor_partial", Artifact.kind == "log"
                )
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            out_lines = [line for line in content["lines"] if line["tag"] == "OUT"]
            assert [line["text"] for line in out_lines] == [first]
            assert content["live"] == remainder.strip()

            streamer.append_stream_line(json.dumps({"type": "result", "result": complete}))
            session.refresh(artifact)
            content = json.loads(artifact.content_json)
            out_lines = [line for line in content["lines"] if line["tag"] == "OUT"]
            assert [line["text"] for line in out_lines] == [first, remainder.strip()]
            assert content["live"] is None
    finally:
        stream_mod.engine = original_engine


def test_run_log_streamer_coalesces_cursor_thinking_deltas():
    """Cursor stream-partial-output emits thinking subtype=delta token events.

    These used to fall through format_stream_payload's generic text handler and
    flush one OUT line per word.
    """
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

            title_chunk = "**Organizing ticket processes**\n\nI"
            body = "need to start with the MCP ticket. Is there a workflow module already embedded?"
            streamer = RunLogStreamer(
                run_id="run_cursor_thinking",
                ticket_id=ticket.id,
                run_code="run_cursor_thinking",
                agent_id="planner",
                skill_name="plan",
                partial_output=True,
            )
            # First delta includes the thinking title + paragraph break + first word,
            # matching real cursor-agent stream-json.
            deltas = [
                title_chunk,
                " need",
                " to",
                " start",
                " with",
                " the",
                " MCP",
                " ticket",
                ".",
                " Is",
                " there",
                " a",
                " workflow",
                " module",
                " already",
                " embedded",
                "?",
            ]
            for text in deltas:
                streamer.append_stream_line(
                    json.dumps({"type": "thinking", "subtype": "delta", "text": text})
                )

            artifact = session.exec(
                select(Artifact).where(
                    Artifact.run_id == "run_cursor_thinking", Artifact.kind == "log"
                )
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            out_texts = [line["text"] for line in content["lines"] if line["tag"] == "OUT"]
            assert out_texts == [title_chunk, body]
            assert not any(text in {"need", "to", "start", "MCP"} for text in out_texts)
            assert content["live"] is None

            streamer.append_stream_line(json.dumps({"type": "thinking", "subtype": "completed"}))
            session.refresh(artifact)
            content = json.loads(artifact.content_json)
            out_texts = [line["text"] for line in content["lines"] if line["tag"] == "OUT"]
            assert out_texts == [title_chunk, body]
            assert content["live"] is None
    finally:
        stream_mod.engine = original_engine


def test_format_stream_payload_ignores_thinking_deltas():
    assert format_stream_payload({"type": "thinking", "subtype": "delta", "text": " need"}) is None


def test_run_log_streamer_drops_repeated_cursor_message_snapshot():
    """Cursor closes a partial-output message by re-emitting it whole.

    The token deltas already streamed it, so appending the snapshot printed every
    assistant message twice.
    """
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

            message = "Plan attached as artifact abc123. No files changed."
            streamer = RunLogStreamer(
                run_id="run_snapshot",
                ticket_id=ticket.id,
                run_code="run_snapshot",
                agent_id="planner",
                skill_name="plan",
                partial_output=True,
            )

            def assistant(text: str) -> str:
                return json.dumps(
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
                )

            for index, token in enumerate(message.split(" ")):
                streamer.append_stream_line(assistant(token if index == 0 else f" {token}"))
            # cursor's terminal snapshot for the same message
            streamer.append_stream_line(assistant(message))
            streamer.append_stream_line(json.dumps({"type": "result", "result": message}))

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_snapshot", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            out_text = "".join(line["text"] for line in content["lines"] if line["tag"] == "OUT")
            assert out_text.count("Plan attached as artifact") == 1
            assert out_text == message
    finally:
        stream_mod.engine = original_engine


def test_run_log_streamer_finalize_is_idempotent():
    """The executor and run_completion both finalize the same run.

    The second call must not append a second terminal marker or repeat stderr.
    """
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
                run_id="run_finalize",
                ticket_id=ticket.id,
                run_code="run_finalize",
                agent_id="planner",
                skill_name="plan",
            )
            streamer.start("cursor-agent agent -p")
            streamer.finalize(status=RunStatus.FAILED, stderr="boom")

            second = RunLogStreamer(
                run_id="run_finalize",
                ticket_id=ticket.id,
                run_code="run_finalize",
                agent_id="planner",
                skill_name="plan",
            )
            second._hydrate()
            second.finalize(status=RunStatus.FAILED, stderr="boom")

            artifact = session.exec(
                select(Artifact).where(Artifact.run_id == "run_finalize", Artifact.kind == "log")
            ).first()
            assert artifact is not None
            content = json.loads(artifact.content_json)
            tags = [line["tag"] for line in content["lines"]]
            assert tags.count("FAIL") == 1
            assert [line["text"] for line in content["lines"] if line["tag"] == "ERR"] == ["boom"]
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
