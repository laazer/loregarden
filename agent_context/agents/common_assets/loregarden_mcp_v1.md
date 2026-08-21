---
description: Loregarden control plane — tools for ticket workflow, stages, approvals, and artifacts.
globs: []
---
# LOREGARDEN CONTROL PLANE

When Loregarden orchestrates work (IDE, API, or autopilot), **use Loregarden's control-plane
tools** for all ticket data. Tickets live in Loregarden's database and are reachable only
through those tools — they are not files in the repo.

Sections below marked for one transport are rendered only into a run that actually has it.
The run context at the top of your prompt states which one you are on; there is exactly one.

## No markdown deliverables in the repo

**Never create a markdown file to report your work.** No `TEST_*_FINDINGS.md`, no
`*_STAGE_COMPLETION.md`, no `TICKET_*_REPORT.md`, no summary, analysis, sign-off, or
verification file — not at the repo root, not in `server/tests/`, not anywhere. Loregarden
reads none of them, so a report written to disk is invisible to the control plane; it only
gets swept into an unrelated ticket's commit by the orchestrator.

If your role says to "produce a report", "document your findings", or "provide a summary" and
names no destination, the destination is **a control-plane tool** — never a new file:

| You want to record… | Use | Not |
|---|---|---|
| Findings, analysis, test output, review report | `loregarden_attach_artifact` | a `*.md` file |
| Stage outcome on a **stage run** (pass / reject / blocked) | `<<<LOREGARDEN_STAGE_REPORT>>>` sentinel in your response | `loregarden_complete_stage` or a `*_COMPLETION.md` file |
| Assumption or ambiguity you resolved alone | `loregarden_append_checkpoint` | a checkpoint `.md` |
| Ticket learnings, anti-patterns | `loregarden_append_learning` | `LEARNINGS.md` / `learning-output.md` |
| Spec, description, acceptance criteria | `loregarden_update_ticket` | a spec `.md` |
| Handoff to the next stage | `loregarden_write_handoff` | a hand-written YAML |

Short findings belong **in your response text**. Long ones belong in
`loregarden_attach_artifact`. Writing real source code and real test files is of course still
your job — this rule is about *reports about* the work, not the work.

<!-- loregarden:transport=mcp -->
## Transport — native MCP tools

The **`loregarden` MCP server is pre-configured** for this run (HTTP at `{MCP_URL}` while the
dev server is up, or stdio via `scripts/mcp-server.sh`). Call **native MCP tools** directly —
do not reach the HTTP endpoint via Bash/curl or hand-written JSON-RPC.

- **Claude Code tool names:** `mcp__loregarden__<tool>` (e.g. `mcp__loregarden__loregarden_get_ticket`)
- Config uses `"type": "stdio"` or `"type": "http"` — never a bare `url` alone (Claude Code
  schema validation fails).

## Permission bridge

CLI permission prompts (Bash, AskUserQuestion, etc.) route to the Loregarden **approval inbox**
automatically. Resolve approvals in the IDE Triage or Inbox tabs — the agent run resumes after
approval.

## Failure handling

If the MCP tools error or the server is unreachable, the same tools also run in-process from
Bash: `./scripts/loregarden-cli.sh mcp call <tool> key=value…` (`mcp list` / `mcp describe
<tool>` for the arguments). Only if that also fails: log the error in your output and continue
read-only work where possible. Do not invent workflow state — escalate via checkpoint protocol
or block the ticket with a clear message.
<!-- /loregarden:transport -->

<!-- loregarden:transport=cli -->
## Transport — Bash CLI

This run has **no `mcp__loregarden__*` tools**. Every control-plane tool runs in-process
against the database from Bash, so no server has to be up:

```bash
./scripts/loregarden-cli.sh mcp list                    # every tool + description
./scripts/loregarden-cli.sh mcp describe loregarden_get_ticket    # its arguments
./scripts/loregarden-cli.sh mcp call loregarden_get_ticket ticket_id=<id> workspace_slug=<slug>
```

- The wrapper script exists when your cwd is the **loregarden checkout**; when the package is
  installed, the same command is just `loregarden mcp …`.
- Arguments are `key=value`, typed from each tool's own schema. `describe` tells you the
  accepted names — a wrong name is rejected with the list of valid ones, so guess nothing.
- For long values (`content`, artifact bodies), write the text to a file and pass
  `content=@path` — never paste a multi-line body onto the command line.
- Exit codes: `0` ok, `1` the tool failed, `2` you invoked it wrong. Errors go to stderr.

Do **not** hand-write JSON-RPC against the HTTP endpoint, and do not abandon a control-plane
write because no MCP tool is listed.

## Failure handling

If a call fails, read stderr and fix the invocation — exit `2` means the arguments were wrong,
and `describe` lists the accepted ones. Only if the tool itself keeps failing: log the error in
your output and continue read-only work where possible. Do not invent workflow state — escalate
via checkpoint protocol or block the ticket with a clear message.
<!-- /loregarden:transport -->

## Which tool for which situation

| Situation | Tool |
|-----------|------|
| Read current stage map, blocking issues, active orchestration | `loregarden_get_ticket` or `loregarden_get_ticket_by_external` |
| Find tickets by title/slug, list siblings/children, browse workspace | `loregarden_list_tickets` |
| Unrecoverable failure | `loregarden_block_ticket` |
| Human sign-off needed | `loregarden_request_approval` |
| Attach log/diff/test output | `loregarden_attach_artifact` |
| Persist learnings / memory | `loregarden_append_learning`, `loregarden_upsert_memory`, `loregarden_search_memory` |
| Persist blog post markdown | `loregarden_upsert_blog_post` |
| Log a checkpoint (assumption/ambiguity, see `checkpoint_protocol_v1.md`) | `loregarden_append_checkpoint` |
| Inspect memory backend config | `loregarden_memory_status` |

`loregarden_get_ticket` accepts a UUID, or an `external_id` slug when `workspace_slug` is also
given; its response includes a `hierarchy` block (parent, siblings, children). The exact
identifiers for this run are in the run context at the top of your prompt — use those values
rather than searching for them.

**Do not** search the repo for a ticket file. `project_board/` is not a ticket store — it holds
only checkpoint and handoff artifacts. If `loregarden_get_ticket` does not have what you need,
the data does not exist anywhere else.
