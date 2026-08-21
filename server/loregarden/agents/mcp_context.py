"""Loregarden MCP prompt context for agent runs."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    CliAdapter,
    ControlPlaneTransport,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.services.mcp_registry import cli_server_entries
from sqlmodel import Session

logger = logging.getLogger(__name__)

MCP_DOC_REL = Path("agents/common_assets/loregarden_mcp_v1.md")
MEMORY_DOC_REL = Path("agents/common_assets/memory_protocol_v1.md")
WORKFLOW_ENFORCEMENT_DOC_REL = Path("agents/common_assets/workflow_enforcement_v1.md")
UI_PRIMITIVES_DOC_REL = Path("agents/common_assets/ui_primitives_v1.md")
STAGE_REPORT_SECTION_TITLE = "STAGE REPORT CONTRACT"
_SECTION_DIVIDER_RE = re.compile(r"^-{20,}\s*$", re.MULTILINE)
MCP_SERVER_NAME = "loregarden"
CLAUDE_MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"

#: How a CLI-transport run invokes a control-plane tool from Bash. The wrapper
#: runs the tool in-process against the database, so it needs no server.
CLI_TOOL_COMMAND = "./scripts/loregarden-cli.sh mcp call"

#: The adapters this process actually wires its MCP server into — the union of
#: what ``append_mcp_cli_args`` and ``mcp_cli_env`` configure. Both read it, so
#: "which runs have MCP tools" has one answer rather than two that can drift.
MCP_WIRED_ADAPTERS: frozenset[CliAdapter] = frozenset(
    {CliAdapter.CLAUDE, CliAdapter.CURSOR, CliAdapter.CODEX, CliAdapter.OPENCODE}
)

#: Marks a run of prompt-asset text as belonging to one transport. Written as an
#: HTML comment so the asset still reads as ordinary markdown to a human.
_TRANSPORT_BLOCK_RE = re.compile(
    r"[ \t]*<!--\s*loregarden:transport=(?P<transport>\w+)\s*-->\n"
    r"(?P<body>.*?)"
    r"[ \t]*<!--\s*/loregarden:transport\s*-->\n?",
    re.DOTALL,
)


def _adapter_or_none(adapter: str) -> CliAdapter | None:
    """The adapter enum for a raw pin, or None when it names no known runner.

    An operator's ``LOREGARDEN_CLI_ADAPTER`` override is free text, so this has
    to answer "not one of ours" rather than raise.
    """
    try:
        return CliAdapter(adapter)
    except ValueError:
        return None


def resolve_control_plane_transport(*, run: AgentRun, adapter: str) -> ControlPlaneTransport:
    """The channel ``run`` will actually reach the control plane through.

    Read off the wiring, never off a preference. Three things decide it, and
    each is a fact about this run rather than a policy about a kind of run:

    - An externally driven run is executed by a harness this process never
      spawns, so nothing here attaches an MCP client to it. Whatever that
      harness configures for itself, the control plane cannot assert it — and
      asserting it is what produced a prompt telling the agent to call tools it
      did not have.
    - ``LOREGARDEN_DISABLE_MCP_CLI`` turns the injection off wholesale.
    - Only ``MCP_WIRED_ADAPTERS`` get an MCP server wired in at all; a run on any
      other runner reaches the database through the CLI wrapper.
    """
    if run.external_harness is not None:
        return ControlPlaneTransport.CLI
    if not mcp_cli_injection_enabled():
        return ControlPlaneTransport.CLI
    if _adapter_or_none(adapter) not in MCP_WIRED_ADAPTERS:
        return ControlPlaneTransport.CLI
    return ControlPlaneTransport.MCP


def tool_reference(name: str, transport: ControlPlaneTransport) -> str:
    """How the agent invokes control-plane tool ``name`` on its own transport."""
    if transport is ControlPlaneTransport.MCP:
        return f"`{CLAUDE_MCP_TOOL_PREFIX}{name}`"
    return f"`{CLI_TOOL_COMMAND} {name}`"


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


def resolve_api_base_url() -> str:
    """Base URL of this control plane's HTTP API, for commands run outside it.

    Mirrors ``resolve_mcp_url``'s precedence so a terminal-handoff command and
    its MCP config always point at the same server instance.
    """
    api_base = os.environ.get("LOREGARDEN_API_URL")
    if api_base:
        return api_base.rstrip("/")
    return settings.mcp_url.rstrip("/").removesuffix("/mcp")


def _default_mcp_transport() -> str:
    explicit = os.environ.get("LOREGARDEN_MCP_TRANSPORT", "").strip().lower()
    if explicit:
        return explicit
    # Stage runs execute while the Loregarden API is up — HTTP avoids stdio cold start.
    return "http"


def loregarden_mcp_server_entry(*, orchestrated: bool = False) -> dict[str, Any]:
    transport = _default_mcp_transport()
    if transport == "http":
        entry: dict[str, Any] = {"type": "http", "url": resolve_mcp_url()}
        if orchestrated:
            entry["headers"] = {"X-Loregarden-Orchestrated": "1"}
        return entry
    script = settings.repo_root / "scripts" / "mcp-server.sh"
    env = {
        "LOREGARDEN_MCP_INPROCESS": "1",
        "LOREGARDEN_REPO_ROOT": str(settings.repo_root),
    }
    if orchestrated:
        env["LOREGARDEN_MCP_ORCHESTRATED"] = "1"
    return {
        "type": "stdio",
        "command": str(script),
        "args": [],
        "env": env,
    }


def loregarden_mcp_cli_config_json(
    session: Session | None = None, *, orchestrated: bool = False
) -> str:
    """Claude Code `--mcp-config` payload (full settings shape with mcpServers).

    Loregarden's own server plus whatever is registered and enabled. Without a
    session the payload is loregarden alone, which is what callers outside a
    request (docs, tests) want and what this always used to return.

    Loregarden's entry is written last on purpose: a registered server may not
    take its name, because losing the control plane's own tools would break the
    workflow the agent is running.

    `orchestrated` marks this config as belonging to a run Loregarden itself
    supervises (see `execute_tool`'s docstring in `mcp/tools.py`) — set it only for
    invocations built by `agents.cli_adapters.resolve_cli_invocation`, never for a
    human terminal handoff or Ticket Studio chat.
    """
    servers: dict[str, dict] = {}
    if session is not None:
        try:
            servers.update(cli_server_entries(session))
        except Exception:  # noqa: BLE001 - a bad registry must not stop a run
            logger.warning("Could not read the MCP registry; using loregarden only", exc_info=True)
    servers[MCP_SERVER_NAME] = loregarden_mcp_server_entry(orchestrated=orchestrated)
    return json.dumps({"mcpServers": servers})


def mcp_cli_injection_enabled() -> bool:
    return os.environ.get("LOREGARDEN_DISABLE_MCP_CLI", "").lower() not in {"1", "true", "yes"}


def _opencode_mcp_server_entry(*, orchestrated: bool) -> dict[str, Any]:
    """Loregarden's entry in OpenCode's own MCP config vocabulary.

    OpenCode names the two transports ``remote``/``local`` and puts a stdio
    server's argv in a single ``command`` list with ``environment`` beside it —
    none of which matches the shape ``loregarden_mcp_server_entry`` renders for
    Claude Code. Translating here keeps that difference in one place.
    """
    entry = loregarden_mcp_server_entry(orchestrated=orchestrated)
    if entry["type"] == "http":
        remote: dict[str, Any] = {"type": "remote", "url": entry["url"], "enabled": True}
        if entry.get("headers"):
            remote["headers"] = entry["headers"]
        return remote
    return {
        "type": "local",
        "command": [entry["command"], *entry["args"]],
        "enabled": True,
        "environment": entry["env"],
    }


def loregarden_mcp_opencode_config_json(*, orchestrated: bool = False) -> str:
    """``OPENCODE_CONFIG_CONTENT`` payload wiring Loregarden's MCP server in.

    OpenCode has no ``--mcp-config`` flag; an inline config is the only per-run
    channel (``opencode mcp add`` writes the operator's own config file, which a
    stage run has no business editing). The registry is deliberately not merged
    in the way ``loregarden_mcp_cli_config_json`` does it: OpenCode reads the
    operator's own config too, so registered servers reach the agent from there
    and duplicating them here would fight that file rather than extend it.
    """
    return json.dumps(
        {"mcp": {MCP_SERVER_NAME: _opencode_mcp_server_entry(orchestrated=orchestrated)}}
    )


def mcp_cli_env(*, adapter: str, orchestrated: bool = False) -> dict[str, str]:
    """Environment an agent subprocess needs to see Loregarden's MCP server.

    The argv-based counterpart is ``append_mcp_cli_args``; opencode is the one
    adapter that configures MCP through the environment instead, so it is the
    only adapter this returns anything for.
    """
    if not mcp_cli_injection_enabled() or adapter != "opencode":
        return {}
    return {
        "OPENCODE_CONFIG_CONTENT": loregarden_mcp_opencode_config_json(orchestrated=orchestrated)
    }


def append_mcp_cli_args(
    argv: list[str],
    *,
    adapter: str,
    session: Session | None = None,
    orchestrated: bool = False,
) -> None:
    """Inject Loregarden MCP into headless Claude/Cursor/Codex agent subprocesses.

    ``orchestrated=True`` marks pipeline stage runs (denies create_ticket at the
    MCP dispatch layer). Chat surfaces — triage, Home, branch triage, Ticket
    Studio — must pass ``orchestrated=False`` so interactive MCP stays open.

    opencode is absent by design: it has no MCP flag, so its config rides in the
    subprocess environment instead — see ``mcp_cli_env``.
    """
    if not mcp_cli_injection_enabled():
        return
    if _adapter_or_none(adapter) not in MCP_WIRED_ADAPTERS:
        return
    if adapter == "claude":
        argv.extend(
            ["--mcp-config", loregarden_mcp_cli_config_json(session, orchestrated=orchestrated)]
        )
    elif adapter == "cursor" and "--approve-mcps" not in argv:
        argv.append("--approve-mcps")
    elif adapter == "codex":
        # Headless `codex exec` has no interactive MCP approval surface; without
        # in-process + auto-approve, every tool call becomes "user cancelled".
        # Chat and stage runs share that need — only the orchestrated env flag
        # differs (pipeline deny list).
        script = settings.repo_root / "scripts" / "mcp-server.sh"
        env = {
            "LOREGARDEN_MCP_INPROCESS": "1",
            "LOREGARDEN_REPO_ROOT": str(settings.repo_root),
        }
        if orchestrated:
            env["LOREGARDEN_MCP_ORCHESTRATED"] = "1"
        argv.extend(
            [
                "-c",
                f"mcp_servers.{MCP_SERVER_NAME}.command={json.dumps(str(script))}",
                "-c",
                f"mcp_servers.{MCP_SERVER_NAME}.args=[]",
                "-c",
                f'mcp_servers.{MCP_SERVER_NAME}.default_tools_approval_mode="approve"',
            ]
        )
        for key, value in env.items():
            argv.extend(
                [
                    "-c",
                    f"mcp_servers.{MCP_SERVER_NAME}.env.{key}={json.dumps(value)}",
                ]
            )


def select_transport_blocks(text: str, transport: ControlPlaneTransport) -> str:
    """Keep only the marked passages that are true on ``transport``.

    An unmarked passage is transport-neutral and always survives. A passage
    marked for the other transport is not trimmed for length — it is removed
    because it describes a channel this run does not have.
    """

    def _keep(match: re.Match[str]) -> str:
        if match.group("transport") == transport.value:
            return match.group("body")
        return ""

    return _TRANSPORT_BLOCK_RE.sub(_keep, text).strip()


def load_loregarden_mcp_doc(agent_context_dir: Path, *, transport: ControlPlaneTransport) -> str:
    """The MCP module, rendered for the channel this run actually has."""
    path = agent_context_dir / MCP_DOC_REL
    if not path.is_file():
        return ""
    return select_transport_blocks(path.read_text(encoding="utf-8"), transport)


def load_memory_protocol_doc(agent_context_dir: Path) -> str:
    path = agent_context_dir / MEMORY_DOC_REL
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_ui_primitives_doc(agent_context_dir: Path) -> str:
    path = agent_context_dir / UI_PRIMITIVES_DOC_REL
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


def tool_invocation(name: str, transport: ControlPlaneTransport, args: dict[str, str]) -> str:
    """A worked call of ``name`` with real values, in this run's own syntax."""
    if transport is ControlPlaneTransport.MCP:
        rendered = ", ".join(f'`{key}="{value}"`' for key, value in args.items())
        return f"{tool_reference(name, transport)} with {rendered}"
    rendered = " ".join(f"{key}={value}" for key, value in args.items())
    return f"`{CLI_TOOL_COMMAND} {name} {rendered}`"


def _transport_header_lines(transport: ControlPlaneTransport) -> list[str]:
    """How this run talks to the control plane, stated once, at the top.

    Only one of these is ever rendered. Handing an agent both is what let a
    supervised run be told about a Bash fallback it has no need of, and an
    externally driven one be told to call MCP tools it was never given.
    """
    if transport is ControlPlaneTransport.MCP:
        return [
            "## Loregarden MCP (required for workflow state)",
            f"The `{MCP_SERVER_NAME}` MCP server is **pre-configured** for this run — "
            "call native MCP tools directly.",
            f"In Claude Code, tools are named `{CLAUDE_MCP_TOOL_PREFIX}<tool>` "
            f"(example: `{CLAUDE_MCP_TOOL_PREFIX}loregarden_get_ticket`).",
            "Do **not** initialize MCP via Bash/curl or manual JSON-RPC.",
            "",
            f"HTTP endpoint (operators only): `{resolve_mcp_url()}`",
        ]
    return [
        "## Loregarden CLI (required for workflow state)",
        "This run has **no MCP tools attached**. Every control-plane tool is reachable "
        "from Bash instead, and runs in-process against the database — no server has "
        "to be up:",
        "",
        "```bash",
        f"{CLI_TOOL_COMMAND.removesuffix(' call')} list                 # every tool + description",
        f"{CLI_TOOL_COMMAND.removesuffix(' call')} describe <tool>      # its arguments",
        f"{CLI_TOOL_COMMAND} <tool> key=value…",
        "```",
        "",
        "Arguments are `key=value`, typed from each tool's own schema — a wrong name "
        "is rejected with the valid ones, so guess nothing. For a long value "
        "(`content`, artifact bodies) write the text to a file and pass "
        "`content=@path`; never paste a multi-line body onto the command line. "
        "Exit codes: `0` ok, `1` the tool failed, `2` you invoked it wrong.",
        "",
        "Do **not** hand-write JSON-RPC against the HTTP endpoint, and do not give up "
        "on a control-plane write because no MCP tool is listed.",
    ]


def _artifact_section_lines(workspace: Workspace, transport: ControlPlaneTransport) -> list[str]:
    return [
        "## Loregarden artifacts (memory, learnings, blog posts, checkpoints)",
        "Workspace-scoped **Obsidian markdown** + optional **SQLite memory graph**. "
        "**Never write files or SQL directly.**",
        f'Always pass `workspace_slug="{workspace.slug}"` on memory tools.',
        "**Discover backends:** "
        + tool_invocation("loregarden_memory_status", transport, {"workspace_slug": workspace.slug})
        + " → Obsidian dirs + `memory_sqlite_path` (graph DB for memory/learnings nodes).",
        "SQLite stores `memory` and `learning` nodes in `memory_nodes` + "
        "`memory_relations`. Blog posts and checkpoints are Obsidian-only.",
        f"**Memory:** {tool_reference('loregarden_upsert_memory', transport)} · "
        f"**learnings:** {tool_reference('loregarden_append_learning', transport)} · "
        f"**blog posts:** {tool_reference('loregarden_upsert_blog_post', transport)} · "
        f"**checkpoints:** {tool_reference('loregarden_append_checkpoint', transport)} "
        "(see checkpoint protocol module below) · "
        f"**graph links:** {tool_reference('loregarden_create_memory_relation', transport)} · "
        f"**search:** {tool_reference('loregarden_search_memory', transport)} "
        "(Obsidian + SQLite).",
        "See Memory protocol module below.",
    ]


def build_mcp_run_context(
    *,
    ticket: Ticket,
    run: AgentRun,
    workspace: Workspace,
    stage_def: WorkflowStageDef | None = None,
    transport: ControlPlaneTransport,
) -> str:
    lines = [
        *_transport_header_lines(transport),
        "",
        "**First call — load ticket state (use these exact values):**",
        "- "
        + tool_invocation(
            "loregarden_get_ticket_by_external",
            transport,
            {"workspace_slug": workspace.slug, "external_id": ticket.external_id},
        ),
        "- or "
        + tool_invocation("loregarden_get_ticket", transport, {"ticket_id": ticket.id})
        + " (UUID), or the same tool with "
        f"`ticket_id={ticket.external_id}` + `workspace_slug={workspace.slug}` (external id)",
        "",
        "**Discover related or other tickets:**",
        "- "
        + tool_invocation("loregarden_list_tickets", transport, {"workspace_slug": workspace.slug})
        + " and optional `search`, `parent_external_id`, or `state` filters",
        "- `loregarden_get_ticket` responses include a `hierarchy` block "
        "(parent, siblings, children)",
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
            "Tickets live in Loregarden's database, not in the repo. Do not search for a ticket",
            "markdown file, and do not write ticket content to one.",
            "",
            "## Stage outcome (stage runs)",
            "This is a **stage run**. Loregarden advances or reroutes from your "
            "`<<<LOREGARDEN_STAGE_REPORT>>>` block when the CLI exits — emit that "
            "sentinel with `pass|fail|needs_rework|blocked` as the last thing in "
            "your response. A clean exit with no report **blocks** the stage.",
            "Do **not** call "
            f"{tool_reference('loregarden_complete_stage', transport)} "
            "(or skip/block orchestration tools) from a stage run — those are for "
            "the orchestrator / autopilot. Attach detail with "
            f"{tool_reference('loregarden_attach_artifact', transport)} when needed.",
            "",
            *_artifact_section_lines(workspace, transport),
            "",
            "## Handoff artifact (workflow gate)",
            *_handoff_section_lines(stage_def, transport),
            "",
            "Available tools: " + ", ".join(_tool_names()),
        ]
    )
    return "\n".join(lines)


def _handoff_section_lines(
    stage_def: WorkflowStageDef | None, transport: ControlPlaneTransport
) -> list[str]:
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
            f"{tool_reference('loregarden_write_handoff', transport)}; a parallel reviewer does not own a",
            "`(from_agent → to_agent)` handoff pair, and inventing one is rejected by the workspace",
            "handoff gate. Record your review through the stage report (and "
            f"{tool_reference('loregarden_attach_artifact', transport)} for detail); the orchestrator runs",
            "the stage-boundary transition gate for you.",
        ]
    return [
        "**Finishing agents:** write `handoff-latest.yaml` via "
        f"{tool_reference('loregarden_write_handoff', transport)} "
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
