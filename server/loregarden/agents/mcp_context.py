"""Loregarden MCP prompt context for agent runs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from loregarden.config import settings
from loregarden.models.domain import AgentRun, Ticket, WorkflowStageDef, Workspace

MCP_DOC_REL = Path("agents/common_assets/loregarden_mcp_v1.md")
MEMORY_DOC_REL = Path("agents/common_assets/memory_protocol_v1.md")
WORKFLOW_ENFORCEMENT_DOC_REL = Path("agents/common_assets/workflow_enforcement_v1.md")
STAGE_REPORT_SECTION_TITLE = "STAGE REPORT CONTRACT"
_SECTION_DIVIDER_RE = re.compile(r"^-{20,}\s*$", re.MULTILINE)
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


def load_stage_report_contract_doc(agent_context_dir: Path) -> str:
    """Body of the workflow-enforcement module's STAGE REPORT CONTRACT section.

    Only that section is injected. The rest of the module is v1-era and
    contradicts the run context agents are already given — it points them at
    ticket markdown files that do not exist, and pins a stage enum that predates
    `workflow_templates`, whose values would poison `reroute_to_stage`.
    """
    path = agent_context_dir / WORKFLOW_ENFORCEMENT_DOC_REL
    if not path.is_file():
        return ""
    # Dividers split the module into alternating title and body chunks, so the
    # section body is the chunk after the one holding the title.
    chunks = _SECTION_DIVIDER_RE.split(path.read_text(encoding="utf-8"))
    for index, chunk in enumerate(chunks[:-1]):
        if chunk.strip().startswith(STAGE_REPORT_SECTION_TITLE):
            return chunks[index + 1].strip()
    return ""


def build_mcp_run_context(
    *,
    ticket: Ticket,
    run: AgentRun,
    workspace: Workspace,
    stage_def: WorkflowStageDef | None = None,
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
            "Use MCP tools for all ticket data — see Loregarden MCP module below.",
            "Tickets live in Loregarden's database, not in the repo. Do not search for a ticket",
            "markdown file, and do not write ticket content to one.",
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
            "## Handoff artifact (workflow gate)",
            *_handoff_section_lines(stage_def),
            "",
            "Available tools: " + ", ".join(_tool_names()),
        ]
    )
    return "\n".join(lines)


def _handoff_section_lines(stage_def: WorkflowStageDef | None) -> list[str]:
    """Handoff instructions for the run's stage.

    A parallel stage's several agents are co-reviewers of one stage, not a chain
    of finishing agents — only the stage boundary itself has a handoff, and it is
    keyed by the stage transition, not by any one reviewer. Telling each parallel
    reviewer to "write a handoff for your pair" makes them invent a downstream
    agent that has no frozen catalog entry (e.g. a code reviewer guessing
    `→ test_breaker`), which the strict handoff gate then rejects. So in a parallel
    stage, direct reviewers to report via the stage report instead of authoring a
    handoff.
    """
    if stage_def is not None and stage_def.stage_type == "parallel":
        return [
            "This is a **parallel review stage** — you are one of several co-reviewers, not the",
            "stage's finishing agent. Do **not** call "
            f"`{CLAUDE_MCP_TOOL_PREFIX}loregarden_write_handoff`; a parallel reviewer does not own a",
            "`(from_agent → to_agent)` handoff pair, and inventing one is rejected by the workspace",
            "handoff gate. Record your review through the stage report (and "
            f"`{CLAUDE_MCP_TOOL_PREFIX}loregarden_attach_artifact` for detail); the orchestrator runs",
            "the stage-boundary transition gate for you.",
        ]
    return [
        f"**Finishing agents:** write `handoff-latest.yaml` via `{CLAUDE_MCP_TOOL_PREFIX}loregarden_write_handoff` "
        "(structured `checklist`, not hand-written YAML). It renders canonical schema, computes the "
        "counters, validates against the workspace handoff gate, and returns violations on FAIL so you "
        "fix and retry before the orchestrator runs the blocking transition gate. Use the exact "
        "`item_key`/`item` labels from the frozen catalog for your `(from_agent → to_agent)` pair.",
    ]


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
