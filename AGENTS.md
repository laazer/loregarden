# PROJECT KNOWLEDGE BASE

Navigational reference for agents working in **loregarden**. For operating rules — how to
behave, what to ask, what not to do — read `CLAUDE.md`.

## OVERVIEW

**loregarden** is an Agent SDLC IDE: a local control plane that orchestrates multi-agent
software development. Work is tracked as tickets in SQLite, run through configurable TDD
pipelines by CLI agents (Claude Code / Cursor), gated by an approval inbox, and exposed over
MCP so external agents participate in the same workflow.

It is a **FastAPI + SQLModel/SQLite** backend and a **React 19 + TypeScript + Vite** frontend,
packaged with **Tauri 2**. There is no game engine here — no `.gd`, `.tscn`, `.blend`, or shader
sources. Hive (`client/src/lib/hive/`, `client/src/components/dashboard/hive/`) is a React/canvas
office simulation that visualizes agent activity; it is ordinary frontend code, not an engine
project, and its tickets belong here.

The control plane runs *itself*: loregarden's own tickets are executed by loregarden's agents.
Expect to find the machinery you are editing running while you edit it.

## STRUCTURE

```
loregarden/
├── server/loregarden/
│   ├── api/          # FastAPI routes (tickets.py is the big one)
│   ├── services/     # Control-plane logic — the heart of the system
│   ├── agents/       # Agent registry, prompt assembly, CLI executors
│   │   └── executors/  # cli.py (prompt build + run), permission_bridge.py (approvals)
│   ├── mcp/          # MCP server + tool definitions (tools.py)
│   ├── models/domain # SQLModel tables + schemas
│   ├── db/           # migrations.py — ordered, version-tracked
│   ├── core/         # state_machine.py — stage/transition parsing
│   └── cli/, skills/
├── client/           # React 19 + TS 6 + Vite 8; Jest 30; oxlint; Zustand
├── src-tauri/        # Tauri 2 desktop shell
├── agent_context/    # Agent role prompts + common_assets (this repo's copy)
├── data/loregarden.db  # THE database — tickets, workflows, runs, artifacts
├── docs/             # Real documentation. docs/AUDIT.md records known weaknesses
├── scripts/          # dev-server.sh, dev-client.sh, mcp-server.sh, init-db.sh
└── .lefthook/scripts/  # Pre-commit gates
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Stage routing / classify | `services/studio_service.py` | `resolve_stage_execution`, `resolve_classify_route`, `_route_match_score` |
| Ticket/stage/state sync | `services/workflow_state.py` | `reconcile_workflow_state` is called from many places; mind its side effects |
| Stage transitions | `services/workflow_routing.py` | `apply_stage_route` — reconcile-then-resolve ordering is deliberate |
| Running a pipeline | `services/orchestration.py`, `services/builtin_orchestrator.py` | Sweeps and commits the working tree |
| Prompt assembly | `agents/executors/cli.py` (~line 345) | Builds the full agent prompt; embeds MCP + memory modules |
| Injected run context | `agents/stage_context.py`, `agents/mcp_context.py` | Text every agent sees, before its role file |
| Approvals / permissions | `agents/executors/permission_bridge.py` | `AUTO_APPROVED_MCP_TOOLS`, agent scope check, auto_approve |
| MCP tools | `mcp/tools.py` | 20 tools; names and schemas |
| Agent → role file map | `agents/registry.py` | `role_file` is resolved against the **workspace's** `agent_context/` |
| Migrations | `db/migrations.py` | Append with the next id; never reorder |
| Schema/stage defs | `models/domain/schemas.py`, `core/state_machine.py` | `WorkflowStageDef`, `ClassifyRoute` |

## THE DATABASE IS THE SOURCE OF TRUTH

This is the single most important fact about this repo, and the one that most often misleads
agents:

- **Tickets** live in `tickets` (SQLite). There is **no ticket markdown**. Do not grep for one.
- **Workflow templates** live in `workflow_templates.stages_json`. The YAML under
  `agent_context/workflows/` is v1-era and is **not** what the current workflows use. Editing it
  changes nothing.
- **Learnings, memory, checkpoints, blog posts** live in the workspace's Obsidian vault + memory
  SQLite, written only via MCP.
- **Artifacts and run logs** live in `artifacts` / `agent_runs`.

Reach all of it through the `loregarden_*` MCP tools. `agent_context/agents/common_assets/loregarden_mcp_v1.md`
is the contract and is embedded into every run's prompt.

## COMMANDS

```bash
# Dev (never run servers ad-hoc; use these)
task dev                       # server + client
task server                    # backend only
task client                    # frontend only

# Backend edits are NOT picked up automatically:
touch server/.self-improve-restart     # reload the backend

# Tests
server/.venv/bin/python -m pytest server/tests/ -q          # backend
cd client && npm test                                       # frontend (Jest)

# Lint / gates (also run on pre-commit via lefthook)
task hooks:py-review           # Ruff
task hooks:py-pylint           # Pylint (diff-scoped)
task hooks:py-organization     # organization guardrails
cd client && npm run lint      # oxlint

# DB
sqlite3 data/loregarden.db
```

> **Running pytest from a git worktree** explodes with `git add .` exit-128 errors (a `GIT_DIR`
> leak in the pre-push suite). Run with the git env unset: `env -u GIT_DIR -u GIT_WORK_TREE …`.

## CONVENTIONS

- **Python**: module-level imports; Pydantic v2 / SQLModel for payloads. Ruff + Pylint enforced on staged files.
- **Test isolation**: prefer `unittest.mock` (`patch`, `MagicMock`) over pytest's `monkeypatch`, unless mocking handles the case poorly (e.g. `os.environ` swaps).
- **Naming**: stable filenames describing behavior. **No ticket IDs in filenames** — `test_classify_routing.py`, not `test_82_findings.py`.
- **Git**: Conventional Commits. `git mv` for renames.
- **Migrations**: append to `MIGRATIONS` with the next id; each migration guards its own changes; never rewrite an applied id.

## ANTI-PATTERNS (THIS PROJECT)

| Pattern | Why forbidden | Evidence |
|---|---|---|
| Writing a report/summary/findings `.md` | Loregarden reads none of them; the orchestrator sweeps them into unrelated commits. Use `loregarden_attach_artifact` | 40 stray files deleted; see `loregarden_mcp_v1.md` → *No markdown deliverables* |
| Searching for a ticket markdown file | Tickets are DB rows. The prompts that implied otherwise are fixed | `agents/stage_context.py` |
| Editing `agent_context/workflows/*.yaml` to change a workflow | v1-era; live templates are in `workflow_templates.stages_json` | `services/studio_service.py` |
| Hand-editing files while an orchestration runs | It commits the **whole** working tree into whatever ticket is open | commit `49096a5` re-added 13 deleted files |
| Ticket IDs in test/doc filenames | Produces the `TICKET_39_*` sprawl that was just deleted | `docs/AUDIT.md` L-3 |
| `str(...).strip().lower()` defensively | Redundant normalization; normalize at the source | `.lefthook/scripts/detect-defensive-normalization.sh` |
| Assuming `alwaysApply: true` in prompt frontmatter does anything | It is a Cursor convention loregarden does not honor. A common asset reaches an agent only if its `role_file` says to read it, or the executor embeds it | `agents/executors/cli.py` |

## NOTES

- **`agent_context/` is per-workspace.** `resolve_agent_context_dir` reads it from the ticket's
  workspace `repo_path`, so a run against another workspace loads that workspace's prompts, not
  these. Do not "fix" a loregarden prompt to satisfy another workspace's ticket.
- **The prompt embed is truncated**: the MCP module is cut at 12000 chars and the memory module
  at 8000 (`agents/executors/cli.py`). Content added near the end of those files can silently
  vanish from the prompt. Check the size after editing them.
- **Complexity hotspots** (>900 lines): `studio_service.py` (1452), `orchestration.py` (1277),
  `usage_service.py` (1167), `permission_bridge.py` (1088), `artifact_service.py` (1049),
  `mcp/tools.py` (1039), `ticket_studio_service.py` (980), `api/tickets.py` (928),
  `builtin_orchestrator.py` (921) — refactoring candidates.
- **Known weaknesses** are catalogued in `docs/AUDIT.md`. Read it before proposing a rewrite.
