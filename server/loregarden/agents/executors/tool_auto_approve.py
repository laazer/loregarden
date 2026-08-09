"""Auto-approve / deny policy for CLI permission prompts."""

from __future__ import annotations

import shlex
from pathlib import PurePath
from typing import Any

from loregarden.mcp.tool_ids import (
    AUTO_APPROVED_MCP_TOOLS,
    ORCHESTRATED_DENIED_MCP_TOOLS,
    TICKET_SCOPED_MCP_TOOLS,
    McpTool,
)
from loregarden.models.domain import Ticket

ASK_USER_QUESTION_TOOL = "AskUserQuestion"

LOREGARDEN_MCP_PREFIX = "mcp__loregarden__"

#: CLI tools approved by policy rather than per call. The stored allowlist keys
#: on the exact tool input, so every distinct URL would otherwise need its own
#: rule and an unattended research run stalls on the first fetch. These only
#: read; they cannot touch the repo or the control plane.
AUTO_APPROVED_CLI_TOOLS = frozenset({"WebFetch", "WebSearch"})


def _git_subcommand_index(tokens: list[str], git_index: int) -> int | None:
    index = git_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-C", "-c", "--git-dir", "--work-tree"}:
            index += 2
            continue
        if token.startswith(("--git-dir=", "--work-tree=")):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index
    return None


def _looks_like_git_executable(token: str) -> bool:
    return PurePath(token).name == "git"


def _has_hook_bypass_flag(args: list[str]) -> bool:
    return any(
        arg == "--no-verify" or (arg.startswith("-") and not arg.startswith("--") and "n" in arg)
        for arg in args
    )


def _has_obvious_hook_bypass_intent(command: str) -> bool:
    normalized = command.replace("\\\n", " ")
    return (
        "git" in normalized
        and "commit" in normalized
        and ("--no-verify" in normalized or " -n" in normalized)
    )


def _git_commit_bypasses_hooks(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return _has_obvious_hook_bypass_intent(command)

    for index, token in enumerate(tokens):
        if not _looks_like_git_executable(token):
            continue
        subcommand_index = _git_subcommand_index(tokens, index)
        if subcommand_index is None or tokens[subcommand_index] != "commit":
            continue
        if _has_hook_bypass_flag(tokens[subcommand_index + 1 :]):
            return True
    return _has_obvious_hook_bypass_intent(command)


def denied_cli_tool_message(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Return a hard-deny reason for CLI tool calls that must never be approved."""
    if tool_name != "Bash":
        return ""
    command = tool_input.get("command")
    if isinstance(command, str) and _git_commit_bypasses_hooks(command):
        return "git commit hook bypass is forbidden; run git commit without --no-verify/-n."
    return ""


def is_auto_approved_cli_tool(tool_name: str) -> bool:
    return tool_name in AUTO_APPROVED_CLI_TOOLS


def bare_mcp_tool_name(tool_name: str) -> str | None:
    if tool_name.startswith(LOREGARDEN_MCP_PREFIX):
        return tool_name[len(LOREGARDEN_MCP_PREFIX) :]
    return None


def is_auto_approved_mcp_tool(tool_name: str) -> bool:
    bare = bare_mcp_tool_name(tool_name)
    if not bare:
        return False
    tool = McpTool.try_parse(bare)
    return tool in AUTO_APPROVED_MCP_TOOLS if tool else False


def is_orchestrated_agent_denied_mcp_tool(tool_name: str) -> bool:
    """`tool_name` may arrive bare (as tests and MCP arguments do) or prefixed
    with `mcp__loregarden__` (as the CLI permission bridge sees it) — accept
    either form."""
    bare = bare_mcp_tool_name(tool_name)
    if bare is None:
        bare = tool_name
    tool = McpTool.try_parse(bare)
    return tool in ORCHESTRATED_DENIED_MCP_TOOLS if tool else False


def enrich_mcp_tool_input(
    *,
    bare_tool: str,
    tool_input: dict[str, Any],
    ticket: Ticket | None,
    workspace_slug: str,
) -> dict[str, Any]:
    """Fill in the ids an agent should not have to guess.

    ``ticket`` is None for workspace-scoped chat, where there is no work item to
    default to — those calls must name their own ticket.
    """
    enriched = dict(tool_input)
    tool = McpTool.try_parse(bare_tool)
    if ticket is not None and tool is not None:
        if tool is McpTool.GET_TICKET and not enriched.get("ticket_id"):
            enriched["ticket_id"] = ticket.id
        if tool is McpTool.GET_TICKET_BY_EXTERNAL and not enriched.get("external_id"):
            enriched["external_id"] = ticket.external_id
        if tool in TICKET_SCOPED_MCP_TOOLS and not enriched.get("ticket_id"):
            enriched["ticket_id"] = ticket.id
    if tool is McpTool.GET_TICKET_BY_EXTERNAL and not enriched.get("workspace_slug"):
        enriched["workspace_slug"] = workspace_slug
    if tool is McpTool.LIST_TICKETS and not enriched.get("workspace_slug"):
        enriched["workspace_slug"] = workspace_slug
    if tool is McpTool.CREATE_TICKET and not enriched.get("workspace_slug"):
        enriched["workspace_slug"] = workspace_slug
    return enriched


def is_ask_user_question(tool_name: str) -> bool:
    return tool_name == ASK_USER_QUESTION_TOOL


def build_ask_user_question_input(
    tool_input: dict[str, Any],
    *,
    answers: dict[str, str | list[str]],
    response: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "questions": tool_input.get("questions", []),
        "answers": answers,
    }
    if response.strip():
        payload["response"] = response.strip()
    return payload


def validate_question_answers(
    tool_input: dict[str, Any],
    answers: dict[str, str | list[str]] | None,
    *,
    response: str = "",
) -> None:
    if response.strip():
        return
    questions = tool_input.get("questions") or []
    if not questions:
        raise ValueError("Question approval is missing question payload")
    if not answers:
        raise ValueError("Answers required for agent questions")
    for item in questions:
        if not isinstance(item, dict):
            continue
        question_text = str(item.get("question") or "").strip()
        if not question_text:
            continue
        answer = answers.get(question_text)
        if isinstance(answer, list):
            if not any(str(part).strip() for part in answer):
                raise ValueError(f"Answer required for: {question_text}")
            continue
        if not str(answer or "").strip():
            raise ValueError(f"Answer required for: {question_text}")
