"""Live chat-turn reasoning: parsing, persistence, and the fold into the message."""

from __future__ import annotations

import json

from loregarden.models.domain import ChatTurnThinking
from loregarden.services.chat_thinking import (
    ChatTurnThinkingSink,
    SettledThinking,
    chat_turn_topic,
    clear_orphaned_chat_turn_thinking,
    finish_chat_turn_thinking,
    read_chat_turn_thinking,
    with_thinking_part,
)
from sqlmodel import Session


def _partial(event: dict) -> str:
    return json.dumps({"type": "stream_event", "event": event})


def _thinking_delta(text: str) -> str:
    return _partial(
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": text}}
    )


def test_thinking_deltas_accumulate_and_persist(db_session: Session):
    sink = ChatTurnThinkingSink("turn-1")
    sink.append_stream_line(
        _partial({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}})
    )
    sink.append_stream_line(_thinking_delta("The suite fails "))
    sink.append_stream_line(_thinking_delta("because of the fixture."))
    sink.close()

    frame = read_chat_turn_thinking(db_session, "turn-1")
    assert frame["content"] == "The suite fails because of the fixture."
    assert frame["activity"] == "Thinking"
    assert frame["seq"] > 0


def test_tool_calls_land_as_steps_with_what_they_are_aimed_at(db_session: Session):
    """The step is written from the completed message: `content_block_start`
    has the tool's name but not yet its arguments, and the arguments are the
    half that says anything."""
    sink = ChatTurnThinkingSink("turn-2")
    sink.append_stream_line(_thinking_delta("Look at the runner."))
    sink.append_stream_line(
        _partial(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "name": "Read", "input": {}},
            }
        )
    )
    sink.append_stream_line(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "a/b.py"}}
                    ]
                },
            }
        )
    )
    sink.close()

    frame = read_chat_turn_thinking(db_session, "turn-2")
    assert "Look at the runner." in frame["content"]
    assert "· Read · a/b.py" in frame["content"]
    assert frame["activity"] == "Read · a/b.py"


def test_completed_assistant_message_is_ignored_once_deltas_are_seen(db_session: Session):
    """Partial messages repeat as a finished block; counting both doubles the text."""
    sink = ChatTurnThinkingSink("turn-3")
    sink.append_stream_line(_thinking_delta("One thought."))
    sink.append_stream_line(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "One thought."}]},
            }
        )
    )
    sink.close()

    assert read_chat_turn_thinking(db_session, "turn-3")["content"] == "One thought."


def test_completed_assistant_message_is_used_when_no_deltas_arrive(db_session: Session):
    """A CLI run without partial messages still has reasoning worth showing."""
    sink = ChatTurnThinkingSink("turn-4")
    sink.append_stream_line(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "Checked the fixture."},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
                    ]
                },
            }
        )
    )
    sink.close()

    frame = read_chat_turn_thinking(db_session, "turn-4")
    assert "Checked the fixture." in frame["content"]
    assert "· Bash · pytest -q" in frame["content"]


def test_the_bridge_contributes_refusals_and_steers_only(db_session: Session):
    """An approval that went through is already visible as the tool call it
    approved; a refusal changes what happens next and is not visible anywhere
    else in the stream."""
    sink = ChatTurnThinkingSink("turn-5")
    sink.append("TOOL", "Auto-approved read-only: Grep")
    sink.append("TOOL", "Denied (out of scope): client/src/app.tsx")
    sink.append("STEER", "check the fixture first")
    sink.append("OUT", "not an activity line")
    sink.close()

    content = read_chat_turn_thinking(db_session, "turn-5")["content"]
    assert "· Denied (out of scope): client/src/app.tsx" in content
    assert "· Steered: check the fixture first" in content
    assert "Auto-approved" not in content
    assert "not an activity line" not in content


def test_unparseable_lines_are_dropped_rather_than_shown(db_session: Session):
    sink = ChatTurnThinkingSink("turn-6")
    sink.append_stream_line("not json at all")
    sink.close()

    assert read_chat_turn_thinking(db_session, "turn-6")["content"] == ""


def test_read_of_an_unknown_turn_is_an_empty_frame(db_session: Session):
    assert read_chat_turn_thinking(db_session, "nope") == {
        "turn_id": "nope",
        "content": "",
        "answer": "",
        "activity": "",
        "seq": 0,
        "plans": [],
    }


def test_the_reply_streams_apart_from_the_reasoning(db_session: Session):
    """A read-only turn emits an empty thinking block, so the reply is the only
    thing that moves — but it must never be mistaken for reasoning."""
    sink = ChatTurnThinkingSink("turn-8")
    sink.append_stream_line(
        _partial({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}})
    )
    sink.append_stream_line(
        _partial({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ship "}})
    )
    sink.append_stream_line(
        _partial({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "it."}})
    )
    sink.close()

    frame = read_chat_turn_thinking(db_session, "turn-8")
    assert frame["answer"] == "Ship it."
    assert frame["content"] == ""
    assert frame["activity"] == "Writing the reply"


def test_codex_agent_updates_stream_and_the_newest_replaces_the_previous(db_session: Session):
    """Codex emits commentary updates and its final answer as separate
    ``agent_message`` items. The update must be visible immediately, while the
    final answer replaces it instead of being appended into one late reply.
    """
    sink = ChatTurnThinkingSink("turn-codex")
    sink.append_stream_line(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "I’ll inspect the runner first."},
            }
        )
    )
    assert read_chat_turn_thinking(db_session, "turn-codex")["answer"] == (
        "I’ll inspect the runner first."
    )

    sink.append_stream_line(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "The runner uses the main checkout."},
            }
        )
    )
    sink.close()

    assert read_chat_turn_thinking(db_session, "turn-codex")["answer"] == (
        "The runner uses the main checkout."
    )


def test_the_streamed_reply_is_not_folded_into_the_message(db_session: Session):
    """The settled message is the reply; a second copy on the thinking part
    would show the same text twice, forever."""
    sink = ChatTurnThinkingSink("turn-9")
    sink.append_stream_line(_thinking_delta("Reasoned."))
    sink.append_stream_line(
        _partial(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ship it."}}
        )
    )
    sink.close()

    transcript = finish_chat_turn_thinking(db_session, "turn-9").transcript
    assert transcript == "Reasoned."
    assert "Ship it." not in transcript


def test_finish_returns_the_transcript_and_drops_the_row(db_session: Session):
    sink = ChatTurnThinkingSink("turn-7")
    sink.append_stream_line(_thinking_delta("  Reasoned about it.  "))
    sink.close()

    assert finish_chat_turn_thinking(db_session, "turn-7").transcript == "Reasoned about it."
    assert db_session.get(ChatTurnThinking, "turn-7") is None
    # Idempotent: the crash path can settle a turn a second time.
    assert finish_chat_turn_thinking(db_session, "turn-7") == SettledThinking()


def test_thinking_part_leads_the_message_parts():
    parts_json = json.dumps([{"primitive": "text", "content": "Done."}])
    folded = json.loads(
        with_thinking_part(parts_json, SettledThinking("Worked it out."), content="Done.")
    )
    assert folded[0] == {"primitive": "thinking", "content": "Worked it out.", "collapsed": True}
    assert folded[1]["primitive"] == "text"


def test_empty_thinking_leaves_the_parts_untouched():
    parts_json = json.dumps([{"primitive": "text", "content": "Done."}])
    assert with_thinking_part(parts_json, SettledThinking("   "), content="Done.") == parts_json


def test_orphaned_rows_are_cleared(db_session: Session):
    db_session.add(ChatTurnThinking(turn_id="dead", content="mid-thought"))
    db_session.commit()

    assert clear_orphaned_chat_turn_thinking(db_session) == 1
    assert db_session.get(ChatTurnThinking, "dead") is None


def test_topic_is_scoped_to_the_turn():
    assert chat_turn_topic("abc") != chat_turn_topic("abd")


def _text_delta(text: str) -> str:
    return _partial({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


def _plan_fence(*checked: bool, plan_id: str = "history-api") -> str:
    items = [
        {"id": f"s{index}", "text": f"Step {index}", "checked": done}
        for index, done in enumerate(checked)
    ]
    card = {"primitive": "todo_list", "owner": "agent", "plan_id": plan_id, "items": items}
    return f"```loregarden\n{json.dumps(card)}\n```"


def _checked(plan: dict) -> list[bool]:
    return [item["checked"] for item in plan["items"]]


def test_a_plan_re_emitted_mid_turn_reaches_the_live_frame(db_session: Session):
    """Baxter re-emits its plan between tool calls; the panel must see each copy."""
    sink = ChatTurnThinkingSink("turn-plan")
    sink.append_stream_line(_text_delta(_plan_fence(True, False) + "\n"))
    sink.append_stream_line(_text_delta("Now step two.\n" + _plan_fence(True, True)))
    sink.close()

    plans = read_chat_turn_thinking(db_session, "turn-plan")["plans"]
    assert [_checked(plan) for plan in plans] == [[True, True]]


def test_a_continuation_attempt_resumes_the_turn_instead_of_rewinding_it(db_session: Session):
    """Readers drop frames older than the last one shown; restarting seq at zero
    froze the panel for the whole continuation and threw away the first
    attempt's plan progress."""
    first = ChatTurnThinkingSink("turn-cont")
    first.append_stream_line(_text_delta(_plan_fence(True, False)))
    first.close()
    seq_after_first = read_chat_turn_thinking(db_session, "turn-cont")["seq"]

    second = ChatTurnThinkingSink("turn-cont")
    second.append_stream_line(_thinking_delta("Carrying on."))
    second.close()

    frame = read_chat_turn_thinking(db_session, "turn-cont")
    assert frame["seq"] > seq_after_first
    assert [_checked(plan) for plan in frame["plans"]] == [[True, False]]


def test_settling_folds_in_a_plan_the_final_reply_did_not_re_emit(db_session: Session):
    """The CLI's settled `result` is only the last message, so a plan checked off
    between tool calls is otherwise lost and the card stays at 0/n."""
    sink = ChatTurnThinkingSink("turn-fold")
    sink.append_stream_line(_text_delta(_plan_fence(True, True, False)))
    sink.close()

    settled = finish_chat_turn_thinking(db_session, "turn-fold")
    final = json.dumps([{"primitive": "text", "content": "Two of three done."}])
    parts = json.loads(with_thinking_part(final, settled, content="Two of three done."))

    assert [part["primitive"] for part in parts] == ["text", "todo_list"]
    assert _checked(parts[1]) == [True, True, False]


def test_settling_keeps_the_final_replys_own_copy_of_the_plan(db_session: Session):
    sink = ChatTurnThinkingSink("turn-final")
    sink.append_stream_line(_text_delta(_plan_fence(True, False)))
    sink.close()

    settled = finish_chat_turn_thinking(db_session, "turn-final")
    final_plan = json.loads(_plan_fence(True, True).split("\n")[1])
    parts = json.loads(with_thinking_part(json.dumps([final_plan]), settled, content=""))

    assert [_checked(part) for part in parts] == [[True, True]]


def test_a_failed_turn_keeps_its_prose_beside_the_folded_plan(db_session: Session):
    """A failed turn's parts start empty; a card with no text part renders without
    the message's content, so the failure reason has to become a part too."""
    sink = ChatTurnThinkingSink("turn-failed")
    sink.append_stream_line(_text_delta(_plan_fence(True, False)))
    sink.close()

    settled = finish_chat_turn_thinking(db_session, "turn-failed")
    parts = json.loads(with_thinking_part("[]", settled, content="Interrupted."))

    assert parts[0] == {"primitive": "text", "content": "Interrupted."}
    assert _checked(parts[1]) == [True, False]
