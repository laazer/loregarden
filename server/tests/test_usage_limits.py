"""Provider usage limits must read as usage limits, not as agent defects."""

from datetime import datetime, timezone

from loregarden.models.domain import RunStatus
from loregarden.services.cli_auth_errors import format_agent_unavailable
from loregarden.services.run_completion import run_usage_limit
from loregarden.services.usage_limits import (
    detect_usage_limit,
    format_usage_limit_hint,
    usage_limit_blocking_issue,
)

CODEX_MESSAGE = (
    "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
    "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try "
    "again at Aug 12th, 2026 10:07 AM."
)


def test_detects_codex_limit_with_absolute_reset():
    limit = detect_usage_limit(CODEX_MESSAGE)

    assert limit is not None
    assert limit.provider == "codex"
    assert limit.reset_text == "Aug 12, 2026 10:07 AM"
    assert "usage limit" in limit.quote.lower()


def test_detects_claude_epoch_reset():
    limit = detect_usage_limit("Claude AI usage limit reached|1786530420")

    assert limit is not None
    assert limit.provider == "claude"
    assert limit.reset_at == datetime.fromtimestamp(1786530420, tz=timezone.utc)


def test_detects_relative_reset_and_resolves_an_instant():
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    limit = detect_usage_limit(
        "cursor-agent: rate limit exceeded. Try again in 4 hours 12 minutes.", now=now
    )

    assert limit is not None
    assert limit.provider == "cursor"
    assert limit.reset_at == datetime(2026, 8, 8, 16, 12, tzinfo=timezone.utc)


def test_ignores_unrelated_limit_wording():
    assert detect_usage_limit("Reached the file size limit for this diff") is None
    assert detect_usage_limit("HTTP 429 from the git host") is None
    assert detect_usage_limit("") is None


def test_hint_names_provider_reset_and_the_purchase_link():
    hint = format_usage_limit_hint(detect_usage_limit(CODEX_MESSAGE))

    assert "Codex / ChatGPT" in hint
    assert "Aug 12, 2026 10:07 AM" in hint
    assert "chatgpt.com/codex/settings/usage" in hint
    assert "not a failure of the work" in hint


def test_chat_turn_reports_the_limit_instead_of_the_raw_dump():
    dump = "OpenAI Codex v0.146.1\n--------\n" + ("x" * 2000) + "\n" + CODEX_MESSAGE
    msg = format_agent_unavailable("Baxter", RuntimeError(dump))

    assert "usage limit" in msg.lower()
    assert "Aug 12, 2026 10:07 AM" in msg
    assert "x" * 200 not in msg


def test_blocking_issue_is_one_actionable_line():
    line = usage_limit_blocking_issue(detect_usage_limit(CODEX_MESSAGE))

    assert line.startswith("Usage limit reached on Codex / ChatGPT")
    assert "Aug 12, 2026 10:07 AM" in line
    assert "\n" not in line


def test_failed_run_reads_stderr_but_stdout_prose_needs_a_reset_window():
    prose = "I refactored the usage limit parser and its tests."

    assert run_usage_limit(RunStatus.FAILED, prose, "") is None
    assert run_usage_limit(RunStatus.FAILED, "", CODEX_MESSAGE) is not None
    # Exit 0 with the provider's own message (Claude prints it and exits clean).
    assert run_usage_limit(RunStatus.SUCCEEDED, CODEX_MESSAGE, "") is not None
    assert run_usage_limit(RunStatus.CANCELLED, "", CODEX_MESSAGE) is None
