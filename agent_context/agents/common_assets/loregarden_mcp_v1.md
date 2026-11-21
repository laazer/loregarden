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
A gate (`md_report_check.py`) refuses a new report-shaped `.md` outside the doc directories
at commit and at every stage transition, and names the tool below that takes it.

If your role says to "produce a report", "document your findings", or "provide a summary" and
names no destination, the destination is **a control-plane tool** — never a new file:

| You want to record… | Use | Not |
|---|---|---|
| Findings, analysis, test output, review report | `loregarden_attach_artifact` | a `*.md` file |
| Architecture, decision record, plan for a milestone or initiative (no run needed) | `loregarden_write_document` | a `docs/*.md` file |
| Stage outcome on a **stage run** (pass / reject / blocked) | `<<<LOREGARDEN_STAGE_REPORT>>>` sentinel in your response | `loregarden_complete_stage` or a `*_COMPLETION.md` file |
| A question you would have asked the operator (with the default you proceed on) | `loregarden_ask_operator` | a checkpoint entry nobody reads |
| An assumption that needs nobody's answer | `loregarden_append_checkpoint` | a checkpoint `.md` |
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

If the MCP tools error or the server is unreachable, run the same tool from Bash:
`./scripts/loregarden-cli.sh mcp call <tool> key=value…` (`mcp describe <tool>` for arguments).
If that fails too, see *When the control plane fails* below.
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

If a call fails, read stderr and fix the invocation (exit `2`: `describe` lists the accepted
arguments). If the tool itself keeps failing, see *When the control plane fails* below.
<!-- /loregarden:transport -->

## When the control plane fails

Log the error in your output and continue read-only work where possible. Do not invent
workflow state: escalate via checkpoint protocol or block the ticket with a clear message.

## Docker capacity — reserve before you start containers

One Docker daemon serves every agent on this machine, and nothing enforces sharing at the
daemon. Before `docker compose up`, `docker run`, or a suite that starts containers, **book it**:
`loregarden_reserve_docker_capacity holder_label="<ticket>: e2e suite" footprint=stack`
(`light`/`service`/`stack`/`heavy`, or `cpus` + `memory_mb`). Its description has the full contract:

- `granted`: start, and note `lease_id` and `expires_at`. `queued`: **start nothing**; poll
  `loregarden_docker_capacity_status` after the `poll_after_seconds` you were given (faster polls
  are throttled; reserving again returns the same place). Queued is not a failure, and a `null`
  `estimated_wait_seconds` means unknown, not soon. `rejected`: read `error_kind`
  (`exceeds_capacity`: ask for less; `docker_unavailable`: Docker is down).
- Once up, `loregarden_renew_docker_lease lease_id=<id> compose_project=<project>`: a lease that
  names its containers is checked against Docker before it is reclaimed; one that names nothing
  can be handed away by its clock mid-run. Renew before `expires_at`.
- **Release** (`loregarden_release_docker_capacity`) as soon as the containers are down.
- Not needed if your stage declared a `docker_footprint`: the orchestrator already holds the lease.

## Reference docs (fetch-through cache)

For framework or library docs: **search, then read** — never a guessed URL, never WebFetch.
`loregarden_search_reference` (`query`, `docset`) returns ranked `url`s; omit `docset` to get
docset suggestions, and `docset_ambiguous` lists candidates. Pass a returned `url` to
`loregarden_fetch_reference`, which also takes any public http(s) URL (private addresses are
refused). Pages are cached once for every later run. It never raises: read `error` and `cache`
(a stale copy is labelled). `refresh: true` only with a reason to think the page changed;
`max_chars` trims only what you are handed.

## Which tool for which situation

| Situation | Tool |
|-----------|------|
| Read current stage map, blocking issues, active orchestration | `loregarden_get_ticket` or `loregarden_get_ticket_by_external` |
| Find tickets by title/slug, list siblings/children, browse workspace | `loregarden_list_tickets` |
| List initiatives (cross-workspace parents of milestones; `init-*` ids) | `loregarden_list_tickets` with `work_item_type=initiative`, no `workspace_slug` |
| Create an initiative / attach a milestone to one | `loregarden_create_ticket` with `work_item_type=initiative`, no `workspace_slug`; then `loregarden_update_ticket` on the milestone with `parent=<init-id>` (`""` detaches) |
| Unrecoverable failure | `loregarden_block_ticket` |
| A stage's exit actions need a person | `loregarden_request_approval` (refused when nothing is outstanding; a sign-off is an `operator_judgment` exit action on the stage) |
| A person must authorize an action (force-push, publish a ref, open/close a PR) | End blocked with `blocked_kind: decision` and the exact command as an option; the pick returns as the `Decision — <stage>` artifact and is the authorization. Not `loregarden_request_approval` |
| Attach log/diff/test output | `loregarden_attach_artifact` |
| Store a document (architecture, decision, plan) on any ticket, initiative included; same title = new version | `loregarden_write_document` |
| Find the planning documents your ticket and its ancestors carry | `documents` in `loregarden_get_ticket`, or `loregarden_list_artifacts` |
| Read one artifact in full (`is_latest` says if it was superseded) | `loregarden_read_artifact` |
| Persist learnings / memory | `loregarden_append_learning`, `loregarden_upsert_memory`, `loregarden_search_memory` |
| Find the right documentation page | `loregarden_search_reference` — not a guessed URL |
| Read framework or library documentation | `loregarden_fetch_reference` — not WebFetch |
| Queue an outside project for the reference survey, or ask what was concluded about one | `loregarden_survey_queue` (`enqueue`, `status`, `coverage`, `request_pass`) |
| Run a survey pass: brief, reads, evidence, findings, unknowns, landing on tickets | `loregarden_survey_pass`, `loregarden_survey_record`, `loregarden_survey_write` |
| Run the change you are checking (a branch server or client) | `loregarden_launch_instance`, then `loregarden_instance_status`, then `loregarden_stop_instance`; `loregarden_restart_instance` after editing its code (same id and URL; a launched server ignores `.self-improve-restart`) |
| Persist blog post markdown | `loregarden_upsert_blog_post` |
| Ask the operator, and carry on with your default (see `checkpoint_protocol_v1.md`) | `loregarden_ask_operator`; `loregarden_list_operator_questions` to read them back |
| Log an assumption that needs nobody's answer | `loregarden_append_checkpoint` |
| Inspect memory backend config | `loregarden_memory_status` |

`loregarden_get_ticket` accepts a UUID, or an `external_id` slug when `workspace_slug` is also
given; its response includes a `hierarchy` block (parent, siblings, children). The exact
identifiers for this run are in the run context at the top of your prompt — use those values
rather than searching for them.

**Do not** search the repo for a ticket file. `project_board/` is not a ticket store — it holds
only checkpoint and handoff artifacts. If `loregarden_get_ticket` does not have what you need,
the data does not exist anywhere else.
