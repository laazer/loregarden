"""The channel a chat panel watches while a turn is thinking."""

import json

from loregarden.models.domain import ChatTurnThinking
from loregarden.services.chat_thinking import ChatTurnThinkingSink, finish_chat_turn_thinking
from sqlmodel import Session


def test_reading_a_turn_with_no_reasoning_is_an_empty_frame(client):
    """Not a 404: "nothing live here" is a state the panel renders, not an error."""
    res = client.get("/api/chat-turns/unknown-turn/thinking")

    assert res.status_code == 200
    assert res.json() == {
        "turn_id": "unknown-turn",
        "content": "",
        "answer": "",
        "activity": "",
        "seq": 0,
    }


def test_reading_a_turn_returns_what_it_has_thought_so_far(client, db_session: Session):
    db_session.add(
        ChatTurnThinking(turn_id="t1", content="Halfway there.", activity="Read · a.py", seq=4)
    )
    db_session.commit()

    body = client.get("/api/chat-turns/t1/thinking").json()

    assert body == {
        "turn_id": "t1",
        "content": "Halfway there.",
        "answer": "",
        "activity": "Read · a.py",
        "seq": 4,
    }


def test_the_socket_opens_with_everything_produced_so_far(client, db_session: Session):
    """A panel opened mid-turn, or reopened after a reload, must not start blank."""
    db_session.add(ChatTurnThinking(turn_id="t2", content="Already thought this.", seq=2))
    db_session.commit()

    with client.websocket_connect("/ws/chat-turns/t2") as socket:
        message = socket.receive_json()

    assert message["type"] == "chat_thinking"
    assert message["data"]["content"] == "Already thought this."


def test_the_socket_pushes_reasoning_as_it_arrives(client):
    with client.websocket_connect("/ws/chat-turns/t3") as socket:
        assert socket.receive_json()["data"]["content"] == ""

        sink = ChatTurnThinkingSink("t3")
        sink.append_stream_line(
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": "Working it out."},
                    },
                }
            )
        )
        sink.close()

        pushed = socket.receive_json()

    assert pushed["type"] == "chat_thinking"
    assert pushed["data"]["content"] == "Working it out."


def test_the_socket_is_told_when_the_turn_settles(client, db_session: Session):
    db_session.add(ChatTurnThinking(turn_id="t4", content="Done thinking.", seq=1))
    db_session.commit()

    with client.websocket_connect("/ws/chat-turns/t4") as socket:
        socket.receive_json()
        finish_chat_turn_thinking(db_session, "t4")

        assert socket.receive_json()["type"] == "chat_thinking_done"
