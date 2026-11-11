import json

import pytest
from loregarden.services.run_log_stream import format_stream_payload
from loregarden.services.tool_call_line import command_head, tool_call_lines


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
