"""Loregarden MCP prompt context for agent runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loregarden.config import settings
from loregarden.models.domain import AgentRun, Ticket, Workspace

MCP_DOC_REL = Path("agents/common_assets/loregarden_mcp_v1.md")
MEMORY_DOC_REL = Path("agents/common_assets/memory_protocol_v1.md")
MCP_SERVER_NAME = "loregarden"
CLAUDE_MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"


def _tool_names() -> list[str]:
    from loregarden.mcp.tools import tool_names

    return tool_names()


def resolve_mcp_url() -> str:
    explicit = os.environ.get("LOREGARDEN_MCP_URL")
    if explicit:
        return explicit.rstrip("/")
    api_base = os.environ.get("LOREGARDEN_API_URL")
    if api_base:
        return f"{api_base.rstrip('/')}/mcp"
    return settings.mcp_url.rstrip("/")


def _default_mcp_transport() -> str:
    explicit = os.environ.get("LOREGARDEN_MCP_TRANSPORT", "").strip().lower()
    if explicit:
        return explicit
    # Stage runs execute while the Loregarden API is up — HTTP avoids stdio cold start.
    return "http"


def loregarden_mcp_server_entry() -> dict[str, Any]:
    transport = _default_mcp_transport()
    if transport == "http":
        return {"type": "http", "url": resolve_mcp_url()}
    script = settings.repo_root / "scripts" / "mcp-server.sh"
    return {
        "type": "stdio",
        "command": str(script),
        "args": [],
        "env": {
            "LOREGARDEN_MCP_INPROCESS": "1",
            "LOREGARDEN_REPO_ROOT": str(settings.repo_root),
        },
    }


def loregarden_mcp_cli_config_json() -> str:
    """Claude Code `--mcp-config` payload (full settings shape with mcpServers)."""
    return json.dumps({"mcpServers": {MCP_SERVER_NAME: loregarden_mcp_server_entry()}})


def mcp_cli_injection_enabled() -> bool:
    return os.environ.get("LOREGARDEN_DISABLE_MCP_CLI", "").lower() not in {"1", "true", "yes"}


def append_mcp_cli_args(argv: list[str], *, adapter: str) -> None:
    """Inject Loregarden MCP into headless Claude/Cursor agent subprocesses."""
    if not mcp_cli_injection_enabled():
        return
    if adapter == "claude":
        argv.extend(["--mcp-config", loregarden_mcp_cli_config_json()])
    elif adapter == "cursor" and "--approve-mcps" not in argv:
        argv.append("--approve-mcps")


def load_loregarden_mcp_doc(agent_context_dir: Path) -> str:
    path = agent_context_dir / MCP_DOC_REL
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_memory_protocol_doc(agent_context_dir: Path) -> str:
    path = agent_context_dir / MEMORY_DOC_REL
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def build_mcp_run_context(
    *,
    ticket: Ticket,
    run: AgentRun,
    workspace: Workspace,
) -> str:
    mcp_url = resolve_mcp_url()
    lines = [
        "## Loregarden MCP (required for workflow state)",
        f"The `{MCP_SERVER_NAME}` MCP server is **pre-configured** for this run — call native MCP tools directly.",
        f"In Claude Code, tools are named `{CLAUDE_MCP_TOOL_PREFIX}<tool>` "
        f"(example: `{CLAUDE_MCP_TOOL_PREFIX}loregarden_get_ticket`).",
        "Do **not** initialize MCP via Bash/curl or manual JSON-RPC.",
        "",
        "**First call — load ticket state (use these exact values):**",
        f"- `{CLAUDE_MCP_TOOL_PREFIX}loregarden_get_ticket_by_external` with "
        f'`workspace_slug="{workspace.slug}"`, `external_id="{ticket.external_id}"`',
        f'- or `{CLAUDE_MCP_TOOL_PREFIX}loregarden_get_ticket` with `ticket_id="{ticket.id}"` '
        f'(UUID) or `ticket_id="{ticket.external_id}"` + `workspace_slug="{workspace.slug}"` (slug)',
        "",
        "**Discover related or other tickets:**",
        f'- `{CLAUDE_MCP_TOOL_PREFIX}loregarden_list_tickets` with `workspace_slug="{workspace.slug}"` '
        "and optional `search`, `parent_external_id`, or `state` filters",
        "- `loregarden_get_ticket` responses include a `hierarchy` block (parent, siblings, children)",
        "",
        f"HTTP endpoint (operators only): `{mcp_url}`",
        "",
        f"- ticket_id: `{ticket.id}`",
        f"- external_id: `{ticket.external_id}`",
        f"- workspace_slug: `{workspace.slug}`",
        f"- agent_run: `{run.run_code}` · stage `{run.stage_key}` · agent `{run.agent_id}`",
    ]
    if run.orchestration_run_id:
        lines.append(f"- orchestration_run_id: `{run.orchestration_run_id}`")
    lines.extend(
        [
            "",
            "Use MCP tools for ticket workflow state — see Loregarden MCP module below.",
            "Do not update project_board WORKFLOW STATE for stage cursor Loregarden owns.",
            "",
            "## Loregarden artifacts (memory, learnings, blog posts, checkpoints)",
            "Workspace-scoped **Obsidian markdown** + optional **SQLite memory graph**. **Never write files or SQL directly.**",
            f'Always pass `workspace_slug="{workspace.slug}"` on memory tools.',
            f"**Discover backends:** `{CLAUDE_MCP_TOOL_PREFIX}loregarden_memory_status` with "
            f'`workspace_slug="{workspace.slug}"` → Obsidian dirs + `memory_sqlite_path` (graph DB for memory/learnings nodes).',
            "SQLite stores `memory` and `learning` nodes in `memory_nodes` + `memory_relations`. "
            "Blog posts and checkpoints are Obsidian-only.",
            f"**Memory:** `{CLAUDE_MCP_TOOL_PREFIX}loregarden_upsert_memory` · "
            f"**learnings:** `{CLAUDE_MCP_TOOL_PREFIX}loregarden_append_learning` · "
            f"**blog posts:** `{CLAUDE_MCP_TOOL_PREFIX}loregarden_upsert_blog_post` · "
            f"**checkpoints:** `{CLAUDE_MCP_TOOL_PREFIX}loregarden_append_checkpoint` "
            "(see checkpoint protocol module below) · "
            f"**graph links:** `{CLAUDE_MCP_TOOL_PREFIX}loregarden_create_memory_relation` · "
            f"**search:** `{CLAUDE_MCP_TOOL_PREFIX}loregarden_search_memory` (Obsidian + SQLite).",
            "See Memory protocol module below.",
            "",
            "Available tools: " + ", ".join(_tool_names()),
        ]
    )
    return "\n".join(lines)


def build_mcp_triage_context(
    *, ticket: Ticket, workspace: Workspace, interactive: bool = True
) -> str:
    mcp_url = resolve_mcp_url()
    tool_line = (
        "These MCP tools are wired in and callable directly — use them rather than describing what you would do."
        if interactive
        else "You are advisory in triage — suggest MCP tools the operator or agents should call; do not claim you invoked them."
    )
    return "\n".join(
        [
            "## Loregarden MCP reference",
            f"MCP endpoint: `{mcp_url}`",
            f"ticket_id: `{ticket.id}` · external_id: `{ticket.external_id}` · workspace: `{workspace.slug}`",
            tool_line,
            "Tools: " + ", ".join(_tool_names()),
        ]
    )
