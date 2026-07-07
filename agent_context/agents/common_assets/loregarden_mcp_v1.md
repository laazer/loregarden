---
description: Loregarden MCP — control-plane tools for ticket workflow, stages, approvals, and artifacts.
globs: []
alwaysApply: true
---
# LOREGARDEN MCP

When Loregarden orchestrates work (IDE, API, or autopilot), **use Loregarden MCP tools** for ticket workflow state instead of editing `WORKFLOW STATE` in project_board markdown.

## Transport

When Loregarden starts a stage run, the **`loregarden` MCP server is pre-configured** (HTTP at `{MCP_URL}` when the dev server is running, or stdio via `scripts/mcp-server.sh`). Use **native MCP tools** directly — do not call the HTTP endpoint via Bash/curl.

- **Claude Code tool names:** `mcp__loregarden__<tool>` (e.g. `mcp__loregarden__loregarden_get_ticket`)
- **Stage runs (Claude/Cursor):** native MCP tools — wired via `--mcp-config` / `.cursor/mcp.json`
- **HTTP JSON-RPC (operators / external drivers):** `POST {MCP_URL}` on the Loregarden server (default `http://127.0.0.1:8000/mcp`)
- **Stdio in-process (recommended for local agents):** `scripts/mcp-server.sh` with `LOREGARDEN_MCP_INPROCESS=1`

Use `"type": "stdio"` or `"type": "http"` in MCP config — never bare `url` alone (Claude Code schema validation fails).

## When to use MCP

| Situation | Tool |
|-----------|------|
| Read current stage map, blocking issues, active orchestration | `loregarden_get_ticket` or `loregarden_get_ticket_by_external` |
| Find tickets by title/slug, list siblings/children, browse workspace | `loregarden_list_tickets` |
| Start top-level autopilot / external orchestration | `loregarden_start_orchestration` |
| Mark a stage running before sub-agent work (orchestrator only) | `loregarden_start_stage` |
| Stage succeeded — advance workflow cursor (orchestrator only) | `loregarden_complete_stage` |
| Route back to upstream agent after gate/review failure | `loregarden_complete_stage` with `outcome: reject`, `next_stage_key`, and `next_agent` |
| Skip optional stage | `loregarden_skip_stage` |
| Unrecoverable failure | `loregarden_block_ticket` |
| Human sign-off needed | `loregarden_request_approval` |
| Attach log/diff/test output | `loregarden_attach_artifact` |
| Finish orchestration run | `loregarden_complete_orchestration` |
| Persist learnings / memory (Obsidian + iCloud SQLite) | `loregarden_append_learning`, `loregarden_upsert_memory`, `loregarden_search_memory` |
| Persist blog post markdown | `loregarden_upsert_blog_post` |
| Inspect memory backend config | `loregarden_memory_status` |

## Memory, learnings, and blog posts (workspace-scoped)

Agent artifacts are **per workspace**. Loregarden resolves Obsidian and SQLite paths — agents must **not** write vault files directly.

1. Read `agent_context/agents/common_assets/memory_protocol_v1.md`
2. Call `loregarden_memory_status` with `workspace_slug` from the run prompt
3. Always pass `workspace_slug` on memory tools

| Artifact | Tool | Resolved path field |
|----------|------|---------------------|
| Durable memory | `loregarden_upsert_memory` | `obsidian_memory_dir` + `memory_sqlite_path` (`memory_nodes`) |
| Ticket learnings | `loregarden_append_learning` | `obsidian_learnings_dir` + `memory_sqlite_path` (`memory_nodes`) |
| Blog posts | `loregarden_upsert_blog_post` | `obsidian_blogposts_dir` only (not in SQLite) |
| Graph links | `loregarden_create_memory_relation` | `memory_sqlite_path` (`memory_relations`) |
| Prior context | `loregarden_search_memory` | searches Obsidian notes + SQLite `memory_nodes` |

## Stage-run agents (planner, static_qa, implementers, …)

Loregarden **stage runs** (started from the IDE or `POST /api/tickets/{id}/start`) already update workflow state when the CLI run completes. During a stage run:

1. **Read** ticket state with `loregarden_get_ticket` — do not trust stale project_board markdown alone.
   - UUID: `{"ticket_id": "<uuid>"}`
   - Slug: `{"ticket_id": "03-wire-cli-agent-runner", "workspace_slug": "loregarden"}`
   - Or `loregarden_get_ticket_by_external` / `loregarden_list_tickets` for discovery
2. **Do not** edit project_board `WORKFLOW STATE` / `NEXT ACTION` for stage cursor changes Loregarden owns.
3. **Do** use MCP to attach extra artifacts (`loregarden_attach_artifact`) or request human approval (`loregarden_request_approval`) when your role requires it.
4. **Do** still edit the repo and project_board ticket **content** (description, acceptance criteria, checkpoints) when your role requires it.

## Orchestrator / autopilot

The top-level orchestrator (autopilot skill or external MCP driver) **must** drive transitions via MCP:

- `loregarden_start_stage` before each sub-agent
- `loregarden_complete_stage` / `loregarden_skip_stage` / `loregarden_block_ticket` after gates
- To route **back to an upstream agent** (Blobert-style rework), call `loregarden_complete_stage` with `outcome: "reject"`, `next_stage_key` (e.g. `implementation`), and `next_agent` — workflow templates may also declare `when: reject` transitions
- Never advance Stage in markdown while Loregarden SQLite is authoritative

## Identifiers

Use values from the run prompt:

- `ticket_id` — UUID from Loregarden DB, **or external_id slug** when `workspace_slug` is also provided
- `external_id` — ticket slug (e.g. `03-wire-cli-agent-runner`)
- `workspace_slug` — workspace name in Loregarden
- `run_id` — orchestration run UUID (orchestrator tools only)

Use `loregarden_list_tickets` when you need to discover tickets without knowing the UUID. Responses from `loregarden_get_ticket` include `hierarchy` (parent, siblings, children).

## Permission bridge

CLI permission prompts (Bash, AskUserQuestion, etc.) route to the Loregarden **approval inbox** automatically when using Claude/Cursor adapters without bypass. Resolve approvals in the IDE Triage or Inbox tabs — the agent run resumes after approval.

## Failure handling

If MCP is unreachable, log the error in your output and continue read-only work where possible. Do not invent workflow state — escalate via checkpoint protocol or block the ticket with a clear message.
