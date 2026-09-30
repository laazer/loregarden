"""Writing from a build older than the database it writes to.

Reading a database migrated by newer code is survivable. Writing to it is not:
a build that predates a column writes rows without it, and those rows look
correct until something reads the missing field. That happened — 38 tickets
created by a checkout predating the ticket-number work each carried the column
default of 0, which broke id resolution for all of them and queued a collision
with the next number the current code would issue.

The drift was already detectable on every startup and only logged, so these
tests are about the decision being made rather than reported.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.cli.errors import UsageError
from loregarden.cli.mcp_tools import _is_read_only, refuse_stale_write
from loregarden.mcp.tool_ids import McpTool

AHEAD = ["0096_agent_run_token_usage", "0097_later_still", "0098_later_again", "0099_and_one_more"]


def test_write_is_refused_when_the_database_is_ahead() -> None:
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=AHEAD):
        with pytest.raises(UsageError) as excinfo:
            refuse_stale_write(McpTool.UPDATE_TICKET, allow_stale=False, supervised=False)
    message = str(excinfo.value)
    # Contract rather than wording: the operator must learn WHICH migrations are
    # missing and HOW to proceed deliberately.
    assert "0096_agent_run_token_usage" in message
    assert "--allow-stale" in message


def test_read_is_allowed_when_the_database_is_ahead() -> None:
    """A stale build may still read: nothing it writes can be wrong."""
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=AHEAD) as drift:
        refuse_stale_write(McpTool.GET_TICKET, allow_stale=False, supervised=False)
    drift.assert_not_called()


def test_write_is_allowed_when_the_database_is_not_ahead() -> None:
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=[]):
        refuse_stale_write(McpTool.UPDATE_TICKET, allow_stale=False, supervised=False)


def test_allow_stale_overrides_the_refusal() -> None:
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=AHEAD) as drift:
        refuse_stale_write(McpTool.UPDATE_TICKET, allow_stale=True, supervised=False)
    drift.assert_not_called()


def test_an_unrecognised_tool_name_counts_as_a_write() -> None:
    """Absence of evidence is not read-only: an unknown name gets the strict path."""
    assert _is_read_only("loregarden_not_a_real_tool") is False
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=AHEAD):
        with pytest.raises(UsageError):
            refuse_stale_write("loregarden_not_a_real_tool", allow_stale=False, supervised=False)


def test_known_read_only_tools_are_classified_as_reads() -> None:
    assert _is_read_only(McpTool.GET_TICKET) is True
    assert _is_read_only(McpTool.UPDATE_TICKET) is False


def test_a_supervised_run_cannot_waive_the_refusal() -> None:
    """The waiver is a person's judgment; an agent that was handed it in the
    refusal message took it on 251 of the 364 runs that used it."""
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=AHEAD):
        with pytest.raises(UsageError) as excinfo:
            refuse_stale_write(McpTool.UPDATE_TICKET, allow_stale=True, supervised=True)
    message = str(excinfo.value)
    assert "0096_agent_run_token_usage" in message
    # The supervised refusal must not hand the agent the waiver again.
    assert "pass --allow-stale" not in message


def test_a_supervised_run_passing_allow_stale_still_writes_to_a_current_database() -> None:
    """Agents pass the flag out of habit; ignoring it must not block a sound write."""
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=[]) as drift:
        refuse_stale_write(McpTool.UPDATE_TICKET, allow_stale=True, supervised=True)
    drift.assert_called_once()


def test_a_supervised_read_is_still_allowed_against_a_newer_database() -> None:
    with patch("loregarden.cli.mcp_tools.unknown_migration_ids", return_value=AHEAD) as drift:
        refuse_stale_write(McpTool.GET_TICKET, allow_stale=False, supervised=True)
    drift.assert_not_called()
