"""A failed CLI turn reads as its cause, not as its event stream."""

from __future__ import annotations

import json

from loregarden.services.cli_agent_runner import FAILURE_DETAIL_CAP, cli_failure_detail

# The shape a real expired-OAuth run printed (2026-10-02), trimmed.
_AUTH_FAILURE = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "system", "subtype": "hook_started", "hook_name": "SessionStart:startup"},
        {"type": "system", "subtype": "init", "tools": ["Bash", "Read"] * 40},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Failed to authenticate"}]},
            "error": "authentication_failed",
        },
        {
            "type": "result",
            "is_error": True,
            "result": "Failed to authenticate: OAuth session expired and could not be refreshed",
        },
    )
)


def test_a_stream_failure_reads_as_its_result():
    assert cli_failure_detail(_AUTH_FAILURE, "") == (
        "Failed to authenticate: OAuth session expired and could not be refreshed"
    )


def test_stderr_wins_and_loses_its_colour():
    stderr = (
        "\x1b[33m⚠ Warning: The provided API key is invalid.\x1b[0m\n"
        "The API key was loaded from the CURSOR_API_KEY environment variable."
    )
    detail = cli_failure_detail(_AUTH_FAILURE, stderr)
    assert detail.startswith("⚠ Warning: The provided API key is invalid.")
    assert "\x1b" not in detail


def test_plain_output_passes_through_and_is_capped():
    assert cli_failure_detail("command not found: claude", "") == "command not found: claude"
    long = cli_failure_detail("x" * 5000, "")
    assert len(long) <= FAILURE_DETAIL_CAP + 2
    assert long.endswith("…")


def test_a_stream_with_no_error_says_nothing_rather_than_dump_itself():
    stream = json.dumps({"type": "system", "subtype": "init", "tools": ["Bash"] * 200})
    assert cli_failure_detail(stream, "") == ""
