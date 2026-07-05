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
| Start top-level autopilot / external orchestration | `loregarden_start_orchestration` |
| Mark a stage running before sub-agent work (orchestrator only) | `loregarden_start_stage` |
| Stage succeeded — advance workflow cursor (orchestrator only) | `loregarden_complete_stage` |
| Skip optional stage | `loregarden_skip_stage` |
| Unrecoverable failure | `loregarden_block_ticket` |
| Human sign-off needed | `loregarden_request_approval` |
| Attach log/diff/test output | `loregarden_attach_artifact` |
| Finish orchestration run | `loregarden_complete_orchestration` |

## Stage-run agents (planner, static_qa, implementers, …)

Loregarden **stage runs** (started from the IDE or `POST /api/tickets/{id}/start`) already update workflow state when the CLI run completes. During a stage run:

1. **Read** ticket state with `loregarden_get_ticket` — do not trust stale project_board markdown alone.
2. **Do not** edit project_board `WORKFLOW STATE` / `NEXT ACTION` for stage cursor changes Loregarden owns.
3. **Do** use MCP to attach extra artifacts (`loregarden_attach_artifact`) or request human approval (`loregarden_request_approval`) when your role requires it.
4. **Do** still edit the repo and project_board ticket **content** (description, acceptance criteria, checkpoints) when your role requires it.

## Orchestrator / autopilot

The top-level orchestrator (autopilot skill or external MCP driver) **must** drive transitions via MCP:

- `loregarden_start_stage` before each sub-agent
- `loregarden_complete_stage` / `loregarden_skip_stage` / `loregarden_block_ticket` after gates
- Never advance Stage in markdown while Loregarden SQLite is authoritative

## Identifiers

Use values from the run prompt:

- `ticket_id` — UUID from Loregarden DB
- `external_id` — ticket slug (e.g. `03-wire-cli-agent-runner`)
- `workspace_slug` — workspace name in Loregarden
- `run_id` — orchestration run UUID (orchestrator tools only)

## Permission bridge

CLI permission prompts (Bash, AskUserQuestion, etc.) route to the Loregarden **approval inbox** automatically when using Claude/Cursor adapters without bypass. Resolve approvals in the IDE Triage or Inbox tabs — the agent run resumes after approval.

## Failure handling

If MCP is unreachable, log the error in your output and continue read-only work where possible. Do not invent workflow state — escalate via checkpoint protocol or block the ticket with a clear message.
