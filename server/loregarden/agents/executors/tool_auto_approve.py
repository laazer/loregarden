"""Auto-approve / deny policy for CLI permission prompts."""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import Ticket

ASK_USER_QUESTION_TOOL = "AskUserQuestion"

LOREGARDEN_MCP_PREFIX = "mcp__loregarden__"

_READ_ONLY_MCP_TOOLS = frozenset(
    {
        "loregarden_get_ticket",
        "loregarden_get_ticket_by_external",
        "loregarden_list_tickets",
        "loregarden_memory_status",
        "loregarden_search_memory",
    }
)

# Bookkeeping writes that land only in Loregarden's own stores — the Obsidian
# vault, the memory graph, and the artifacts table. They cannot touch the repo,
# the filesystem outside the vault, or workflow state, so gating them behind a
# human click buys no safety: it just spends the run's timeout budget. Agents are
# now told to route every report through these tools instead of writing markdown
# into the repo, which makes them hot-path rather than incidental.
#
# Deliberately excluded — these mutate workflow state or write repo files, and
# stay gated: complete_stage, skip_stage, block_ticket, update_ticket,
# write_handoff, request_approval, start/complete_orchestration, start_stage.
_CONTROL_PLANE_WRITE_MCP_TOOLS = frozenset(
    {
        "loregarden_append_checkpoint",
        "loregarden_append_learning",
        "loregarden_upsert_memory",
        "loregarden_create_memory_relation",
        "loregarden_upsert_blog_post",
        "loregarden_attach_artifact",
        "loregarden_attach_evidence",
        "loregarden_search_prior_work",
    }
)

AUTO_APPROVED_MCP_TOOLS = _READ_ONLY_MCP_TOOLS | _CONTROL_PLANE_WRITE_MCP_TOOLS

#: Interim allowlist (a9-create-ticket-mcp-tool triage decision), pending
#: a2-per-agent-server-policy's real per-agent x per-server policy table. Every
#: agent run that reaches PermissionBridgeRunner — whether kicked off by the
#: builtin autopilot or a single manually-started stage — is an orchestrated
#: pipeline agent by definition; interactive contexts (Ticket Studio chat, a
#: human's own terminal Claude Code session, direct operator MCP/HTTP calls)
#: never go through this runner at all. Recorded here as debt: this must be
#: superseded, not left to become the de facto permanent policy.
#:
#: This predicate is checked in two places, not one: here, in
#: `_try_fast_approve`, which stops Claude Code from ever placing the call when
#: `--permission-mode`/`--permission-prompt-tool stdio` is in play; and again at
#: the real dispatch entrypoint, `mcp.tools.execute_tool`, which is what a
#: direct HTTP POST to `/mcp` or an `external_mcp`-driven orchestrator actually
#: hits — this runner is never invoked for those callers, so relying on this
#: check alone left `loregarden_create_ticket` fully open to them (confirmed by
#: running `execute_tool` directly with no orchestration context: creation
#: succeeded unconditionally). `execute_tool` sources its own `orchestrated`
#: flag from the `X-Loregarden-Orchestrated` header / `LOREGARDEN_MCP_ORCHESTRATED`
#: env var that only `agents.cli_adapters.resolve_cli_invocation`'s builders
#: attach — see its docstring for exactly which callers that still misses
#: (plain curl, an `external_mcp` orchestrator, Ticket Studio chat).
ORCHESTRATED_DENIED_MCP_TOOLS = frozenset({"loregarden_create_ticket"})

#: CLI tools approved by policy rather than per call. The stored allowlist keys
#: on the exact tool input, so every distinct URL would otherwise need its own
#: rule and an unattended research run stalls on the first fetch. These only
#: read; they cannot touch the repo or the control plane.
AUTO_APPROVED_CLI_TOOLS = frozenset({"WebFetch", "WebSearch"})


def is_auto_approved_cli_tool(tool_name: str) -> bool:
    return tool_name in AUTO_APPROVED_CLI_TOOLS


def bare_mcp_tool_name(tool_name: str) -> str | None:
    if tool_name.startswith(LOREGARDEN_MCP_PREFIX):
        return tool_name[len(LOREGARDEN_MCP_PREFIX) :]
    return None


def is_auto_approved_mcp_tool(tool_name: str) -> bool:
    bare = bare_mcp_tool_name(tool_name)
    return bare in AUTO_APPROVED_MCP_TOOLS if bare else False


def is_orchestrated_agent_denied_mcp_tool(tool_name: str) -> bool:
    """`tool_name` may arrive bare (as tests and MCP arguments do) or prefixed
    with `mcp__loregarden__` (as the CLI permission bridge sees it) — accept
    either form."""
    bare = bare_mcp_tool_name(tool_name)
    if bare is None:
        bare = tool_name
    return bare in ORCHESTRATED_DENIED_MCP_TOOLS


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
    if ticket is not None:
        if bare_tool == "loregarden_get_ticket" and not enriched.get("ticket_id"):
            enriched["ticket_id"] = ticket.id
        if bare_tool == "loregarden_get_ticket_by_external" and not enriched.get("external_id"):
            enriched["external_id"] = ticket.external_id
    if bare_tool == "loregarden_get_ticket_by_external" and not enriched.get("workspace_slug"):
        enriched["workspace_slug"] = workspace_slug
    if bare_tool == "loregarden_list_tickets" and not enriched.get("workspace_slug"):
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
