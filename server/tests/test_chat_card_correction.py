"""A card that fails to parse is corrected once, logged if it stays text, and
re-parsed on read by whatever parser is current."""

from __future__ import annotations

import json
import logging

import pytest
from loregarden.models.domain.chat_primitives import QAPart, TextPart
from loregarden.services.chat_primitives import load_parts_json, settle_reply
from loregarden.services.chat_primitives.parser import parse_reply
from loregarden.services.chat_primitives.repair import correct_rejected_cards, correction_prompt
from sqlmodel import Session

OPTIONS_QA = (
    '```loregarden\n{"primitive":"qa","question":"Which repo?",'
    '"options":[{"id":"lg","label":"loregarden"},{"id":"bl","label":"blobert"}]}\n```'
)
FIXED_QA = (
    '```loregarden\n{"primitive":"qa","items":'
    '[{"id":"repo","question":"Which repo: loregarden or blobert?"}]}\n```'
)
REPLY = f"Before I file it:\n\n{OPTIONS_QA}\n\nThen I will open the PR."


def _answering(answer: str):
    prompts: list[str] = []

    def corrector(prompt: str) -> str:
        prompts.append(prompt)
        return answer

    return corrector, prompts


def test_a_rejected_card_is_replaced_by_its_correction_in_place():
    rejected = parse_reply(REPLY).rejected
    corrector, prompts = _answering(f"Here:\n{FIXED_QA}")

    corrected, still = correct_rejected_cards(REPLY, rejected, corrector)

    assert still == []
    assert corrected == REPLY.replace(OPTIONS_QA, FIXED_QA)
    assert len(prompts) == 1


def test_the_correction_prompt_carries_each_card_and_its_error():
    (fence,) = parse_reply(REPLY).rejected

    prompt = correction_prompt([fence])

    assert fence.fence in prompt
    assert fence.reason in prompt
    assert fence.kind == "qa"
    assert "items" in fence.reason


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("", id="no-fence"),
        pytest.param(f"{FIXED_QA}\n{FIXED_QA}", id="too-many-fences"),
        pytest.param(OPTIONS_QA, id="still-invalid"),
    ],
)
def test_a_correction_that_does_not_parse_or_line_up_changes_nothing(answer: str):
    rejected = parse_reply(REPLY).rejected
    corrector, _ = _answering(answer)

    corrected, still = correct_rejected_cards(REPLY, rejected, corrector)

    assert corrected == REPLY
    assert still == rejected


def test_a_reply_with_no_rejected_card_asks_for_no_correction():
    corrector, prompts = _answering(FIXED_QA)

    settled = settle_reply(Session(), f"Ok\n{FIXED_QA}", workspace_id=None, corrector=corrector)

    assert prompts == []
    assert settled.content == f"Ok\n{FIXED_QA}"


def test_settle_stores_the_corrected_text_and_its_card():
    corrector, _ = _answering(FIXED_QA)

    settled = settle_reply(Session(), REPLY, workspace_id=None, corrector=corrector)

    assert settled.content == REPLY.replace(OPTIONS_QA, FIXED_QA)
    kinds = [part["primitive"] for part in json.loads(settled.parts_json)]
    assert kinds == ["text", "qa", "text"]


def test_a_card_that_stays_text_is_logged_as_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="loregarden.services.chat_primitives.storage"):
        settled = settle_reply(Session(), REPLY, workspace_id=None, corrector=None)

    assert settled.content == REPLY
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_a_failed_correction_turn_keeps_the_reply_and_says_so(caplog):
    def broken(_prompt: str) -> str:
        raise RuntimeError("CLI exited with code 1")

    with caplog.at_level(logging.WARNING, logger="loregarden.services.chat_primitives.storage"):
        settled = settle_reply(Session(), REPLY, workspace_id=None, corrector=broken)

    assert settled.content == REPLY
    # One for the failed turn (with its traceback), one for the card left as text.
    assert [record.levelno for record in caplog.records] == [logging.WARNING, logging.WARNING]
    assert caplog.records[0].exc_info is not None


def test_a_stored_fence_is_reparsed_by_the_current_parser():
    """The row in the report: stored as text because its last `}` was missing."""
    truncated = (
        '```loregarden\n{"primitive":"qa","items":[{"id":"split","question":"Split it?"}]\n```'
    )
    stored = json.dumps(
        [
            {"primitive": "thinking", "content": "read the ticket", "collapsed": True},
            {"primitive": "text", "content": "Two things first:\n\n"},
            {"primitive": "text", "content": truncated},
            {"primitive": "ticket", "ticket_id": "uuid-1", "title": "Resolved at write"},
        ]
    )

    parts = load_parts_json(stored)

    assert [part["primitive"] for part in parts] == ["thinking", "text", "qa", "ticket"]
    assert QAPart.model_validate(parts[2]).items[0].id == "split"
    assert parts[3]["title"] == "Resolved at write"


def test_a_stored_fence_still_invalid_today_stays_text():
    stored = json.dumps([{"primitive": "text", "content": OPTIONS_QA}])

    assert load_parts_json(stored) == [TextPart(content=OPTIONS_QA).model_dump(mode="json")]


def test_a_baxter_turn_settles_with_its_corrected_card(client, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", REPLY)
    monkeypatch.setenv("LOREGARDEN_CARD_CORRECTION_STUB_RESPONSE", FIXED_QA)
    session_id = client.post("/api/workspaces/loregarden/baxter-chat/sessions", json={}).json()[
        "id"
    ]

    sent = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "File it"},
    )
    assert sent.status_code == 202

    snapshot = client.get(f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}").json()
    assistant = snapshot["messages"][-1]
    assert assistant["content"] == REPLY.replace(OPTIONS_QA, FIXED_QA)
    assert [part["primitive"] for part in assistant["parts"]] == ["text", "qa", "text"]
