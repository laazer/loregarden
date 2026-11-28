import json
from collections import Counter
from pathlib import Path

import pytest
from loregarden.models.domain.enums import CliAdapter
from loregarden.services.run_log_stream import TOOL_RESULT_BLOCK, format_stream_payload
from loregarden.services.tool_call_line import (
    CALL_LINE_ADAPTERS,
    RESULT_FAILED,
    command_head,
    tool_call_lines,
)

#: The rendered headline a failed tool result writes. Pinned as a literal here on purpose: the
#: client parses this exact string, so a test that only read the server's own constant back would
#: follow a rename straight past the client.
RESULT_FAILED_HEADLINE = "result · failed"


def _use(name: str, **args: object) -> dict:
    return {"type": "tool_use", "id": "toolu_1", "name": name, "input": args}


@pytest.mark.parametrize(
    ("block", "text"),
    [
        (_use("Read", file_path="/repo/client/src/lib/hive/cast.ts"), "call · Read · hive/cast.ts"),
        (_use("Edit", file_path="a.py", old_string="x", new_string="y"), "call · Edit · a.py"),
        (
            _use("Bash", command="cd /repo && git push origin main", description="Push"),
            "call · Bash · git push: Push",
        ),
        (_use("Bash", command="FOO=1 /usr/bin/pytest -q"), "call · Bash · pytest"),
        (
            _use("WebFetch", url="https://user:pw@docs.example.com/a?q=1"),
            "call · WebFetch · docs.example.com",
        ),
        (
            _use("Task", description="Find the reader", prompt="secret prompt"),
            "call · Task · Find the reader",
        ),
        (_use("Skill", skill="refactor"), "call · Skill · refactor"),
        (
            _use("mcp__loregarden__loregarden_get_ticket", ticket_id="x"),
            "call · mcp__loregarden__loregarden_get_ticket",
        ),
    ],
)
def test_call_line_names_the_tool_and_its_subject_not_its_content(block, text):
    assert tool_call_lines([block]) == [("TOOL", text)]


def test_every_call_in_a_message_gets_a_line_and_text_blocks_none():
    content = [
        {"type": "text", "text": "hi"},
        _use("Read", file_path="a/b/c"),
        _use("Grep", pattern="foo"),
    ]
    assert [line.text for line in tool_call_lines(content)] == [
        "call · Read · b/c",
        "call · Grep · foo",
    ]


def test_an_unreadable_argument_keeps_the_tool_name():
    assert tool_call_lines([_use("Read", file_path=["not", "a", "string"])]) == [
        ("TOOL", "call · Read")
    ]


def test_command_head_skips_a_bare_cd_line_in_a_script():
    assert command_head("cd /repo/worktree\necho '== check'\ngrep -n x f") == "echo"
    assert command_head("cd /repo; git status --short") == "git status"


def test_command_head_survives_an_open_quote():
    assert command_head("echo 'unterminated") == "echo"


def test_format_stream_payload_reads_a_tool_only_assistant_message():
    payload = {"type": "assistant", "message": {"content": [_use("Glob", pattern="**/*.ts")]}}
    assert format_stream_payload(payload) == ("TOOL", "call · Glob · **/*.ts")
    assert json.dumps(payload)  # the payload is plain stream-json


# ---- S1a: the log records which call failed -------------------------------------------------------
#
# `run_log_stream._format_user_event` built `(TOOL / "result").with_body(body)` and dropped the
# `tool_result` block's `is_error`, so a failed call and a clean one produced the identical line
# and no client could tell them apart (lg-hive-office-2784, S1a).
#
# The fixture is a trim of `data/run-logs/run_75d1f3-2cd98e21.out` — every event the real formatter
# turns into a TOOL line, verbatim and in order: 16 `tool_use` calls and 16 `tool_result` events, of
# which 2 carry `is_error: true`, 8 carry `is_error: false`, and 6 carry no `is_error` key at all.
# That last group is the one a careless check inverts, so it is asserted by name below.

FIXTURES = Path(__file__).parent / "fixtures"
_PAYLOAD = FIXTURES / "run_75d1f3_tool_calls.jsonl"
#: The 16 result bodies as the formatter produced them **before** S1a, in payload order.
#: Recorded once and never re-recorded: it is the baseline "bodies unchanged" is measured against,
#: so regenerating it would make the assertion vacuous.
_BASELINE_BODIES = FIXTURES / "run_75d1f3_result_bodies.json"


def _payload_events() -> list[dict]:
    return [json.loads(raw) for raw in _PAYLOAD.read_text().splitlines() if raw.strip()]


def _headline(line) -> str:
    return line.text.split("\n", 1)[0]


def _body(line) -> str:
    head, _, body = line.text.partition("\n")
    return body


def test_the_trimmed_payload_still_holds_the_three_is_error_shapes():
    """A fixture that lost its failures would make every assertion below pass for nothing."""
    shapes = Counter()
    for event in _payload_events():
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == TOOL_RESULT_BLOCK:
                shapes[repr(block.get("is_error"))] += 1
    assert shapes == Counter({"False": 8, "None": 6, "True": 2})


def test_the_real_payload_yields_exactly_two_failed_results_and_fourteen_clean_ones():
    """S1-AC1, driven through the real formatter rather than a hand-written event.

    The criterion says "2 failed and 9 result". 9 is a miscount: `grep -c '"is_error":false'` over
    the raw log returns 9 because one of those occurrences is in the run's final `result` *event*,
    not a `user` event, and the count also ignores the 6 `tool_result` blocks that carry no
    `is_error` key. Driven through `format_stream_payload`, the payload yields 16 result lines:
    2 failed and 14 clean. See the test-design artifact's spec-gap list.
    """
    heads = [_headline(format_stream_payload(e)) for e in _payload_events()]
    assert heads.count(RESULT_FAILED_HEADLINE) == 2
    assert heads.count("result") == 14
    assert sum(1 for h in heads if h.startswith("call · ")) == 16
    # Nothing in this payload falls through to some third result spelling.
    assert all(
        h == "result" or h == RESULT_FAILED_HEADLINE or h.startswith("call · ") for h in heads
    )


def test_the_failed_headline_is_result_then_the_shared_constant():
    """`RESULT_FAILED` is the name the client keys on; a divergent literal is the bug."""
    assert RESULT_FAILED_HEADLINE == f"result · {RESULT_FAILED}"
    assert RESULT_FAILED not in "result"


def test_marking_a_failure_leaves_every_result_body_byte_identical():
    """S1-AC1's "body unchanged": positional, against the pre-S1a baseline.

    Compared by position, not by headline, so the two bodies whose headline *changed* are still
    held to their old text. An implementation that appended a marker to the body, re-ordered the
    blocks, or clipped the failed ones would pass a headline-only assertion and fail here.
    """
    baseline = json.loads(_BASELINE_BODIES.read_text())
    bodies = [
        _body(line)
        for line in (format_stream_payload(e) for e in _payload_events())
        if _headline(line) in {"result", RESULT_FAILED_HEADLINE}
    ]
    assert len(baseline) == 16
    assert bodies == baseline


def _user(*blocks: dict) -> dict:
    return {"type": "user", "message": {"role": "user", "content": list(blocks)}}


def _result(text: str, is_error: object = ...) -> dict:
    block: dict = {"type": TOOL_RESULT_BLOCK, "tool_use_id": "toolu_1", "content": text}
    if is_error is not ...:
        block["is_error"] = is_error
    return block


@pytest.mark.parametrize(
    ("blocks", "headline"),
    [
        pytest.param([_result("ok", False)], "result", id="is_error-false-is-clean"),
        pytest.param([_result("ok")], "result", id="no-is_error-key-is-clean"),
        pytest.param([_result("boom", True)], RESULT_FAILED_HEADLINE, id="is_error-true-fails"),
        # S1's recorded assumption: the log has one line per event, so one error in a multi-block
        # event makes that line failed. A partial success reading as success would break S5.
        pytest.param(
            [_result("ok", False), _result("boom", True)],
            RESULT_FAILED_HEADLINE,
            id="any-error-in-a-multi-block-event-fails",
        ),
        pytest.param(
            [_result("boom", True), _result("ok", False)],
            RESULT_FAILED_HEADLINE,
            id="order-does-not-matter",
        ),
        pytest.param(
            [_result("ok", False), _result("ok")], "result", id="several-clean-blocks-stay-clean"
        ),
    ],
)
def test_is_error_decides_the_headline_and_nothing_else_does(blocks, headline):
    line = format_stream_payload(_user(*blocks))
    assert line is not None
    assert line.tag == "TOOL"
    assert _headline(line) == headline


@pytest.mark.parametrize(
    "value",
    # Only the boolean `True` is a failure. These are the values a truthiness test would accept and
    # an `is not False` test would accept, and neither is what the Claude payload means.
    ["true", "false", "", 0, 1, None, [], {}],
    ids=repr,
)
def test_only_a_boolean_true_is_a_failure(value):
    line = format_stream_payload(_user(_result("ok", value)))
    assert line is not None
    assert _headline(line) == "result"


def test_the_word_error_in_a_result_body_is_not_a_failure():
    """S4 rejects this inference outright: 22,147 live result bodies carry it as ordinary content."""
    line = format_stream_payload(_user(_result("Error: command not found\nexit code 127", False)))
    assert line is not None
    assert _headline(line) == "result"
    assert _body(line) == "Error: command not found\nexit code 127"


def test_a_failed_result_with_an_empty_body_writes_no_line_at_all():
    """Today's behaviour for an empty result body, which the failure marker must not change:
    a blank body is not a result line, so a marker cannot smuggle an empty one into the log."""
    assert format_stream_payload(_user(_result("   ", True))) is None


def test_a_user_event_with_no_tool_result_still_records_the_prompt_by_size():
    """The other two branches of `_format_user_event` are untouched by S1a."""
    line = format_stream_payload({"type": "user", "message": {"content": [{"text": "x" * 40}]}})
    assert line is not None
    assert line.tag == "SYS"
    assert "prompt" in line.text


def test_only_the_claude_adapter_is_expected_to_write_call_lines():
    """S1b: the set lives beside the writer that produces the lines."""
    assert CALL_LINE_ADAPTERS == frozenset({CliAdapter.CLAUDE})
    assert CliAdapter.CURSOR not in CALL_LINE_ADAPTERS
    assert CliAdapter.CODEX not in CALL_LINE_ADAPTERS
