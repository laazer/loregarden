---
description: Loregarden memory protocol — workspace-scoped Obsidian notes and SQLite graph via MCP only.
globs: []
alwaysApply: true
---
# MEMORY PROTOCOL

Loregarden stores agent artifacts in two backends (when configured):

1. **Obsidian markdown** — human-readable notes (memory, learnings, blog posts)
2. **SQLite memory graph** — structured nodes and relations (memory + learnings only)

Agents must **never** write Obsidian files or SQLite databases directly — always use Loregarden MCP tools.

## Dual-write map

| Artifact | MCP tool | Obsidian (per workspace) | SQLite graph (`memory_sqlite_path`) |
|----------|----------|--------------------------|-------------------------------------|
| **Memory** — durable knowledge | `loregarden_upsert_memory` | `{vault}/{memory_subdir}/{workspace}/` | `memory_nodes` row, `node_type: memory` |
| **Learnings** — ticket insights | `loregarden_append_learning` | `{vault}/{learnings_subdir}/{workspace}/` | `memory_nodes` row, `node_type: learning` |
| **Blog posts** — retrospectives | `loregarden_upsert_blog_post` | `{vault}/{blogposts_subdir}/{workspace}/` | **not stored** (Obsidian only) |
| **Checkpoints** — per-run assumption/ambiguity log | `loregarden_append_checkpoint` | `{vault}/{checkpoints_subdir}/{workspace}/{ticket_id}/{run_id}.md` | **not stored** (Obsidian only) |
| **Relations** between nodes | `loregarden_create_memory_relation` | — | `memory_relations` table |

Default Obsidian subdirs: `Loregarden/Memory`, `Loregarden/Learnings`, `Loregarden/BlogPosts`, `Loregarden/Checkpoints`.

Checkpoints differ from the other artifact types: instead of one file per call, `loregarden_append_checkpoint` appends to a single file per `ticket_id` + `run_id`, since a run typically logs several checkpoint entries as it goes.

Default SQLite layout: base config path `…/Loregarden/memory.db` → per workspace `…/Loregarden/{workspace_slug}/memory.db`.

## Discover paths (first call)

Call `loregarden_memory_status` with the run's `workspace_slug`:

```json
{"workspace_slug": "loregarden"}
```

| Field | Meaning |
|-------|---------|
| `obsidian_vault` | Root Obsidian vault |
| `obsidian_memory_dir` | Durable memory markdown |
| `obsidian_learnings_dir` | Ticket learning markdown |
| `obsidian_blogposts_dir` | Blog post markdown |
| `obsidian_checkpoints_dir` | Checkpoint log markdown |
| `memory_sqlite_path` | Per-workspace graph DB file path |
| `memory_sqlite_url` | Same DB as `sqlite:///` URL (operators) |
| `memory_graph_tables` | `memory_nodes`, `memory_relations` |
| `memory_graph_node_types` | `memory`, `learning` (what MCP writes to SQLite) |
| `memory_graph_excludes` | `blog_post`, `checkpoint` — never in SQLite |

`database_path` in the same response is the **Loregarden control-plane** DB (tickets, runs) — not agent memory. Do not confuse it with `memory_sqlite_path`.

## Required identifier

Every memory tool call must include `workspace_slug` from the run prompt.

## Which tool to use

| Goal | Tool | SQLite side effect |
|------|------|-------------------|
| Ticket learnings (Learning Agent) | `loregarden_append_learning` | Upserts `learning` node |
| Durable patterns / anti-patterns | `loregarden_upsert_memory` | Upserts `memory` node |
| Human-readable blog post | `loregarden_upsert_blog_post` | None |
| Per-run assumption/ambiguity log (checkpoint protocol) | `loregarden_append_checkpoint` | None |
| Find prior context | `loregarden_search_memory` | Searches Obsidian + `memory_nodes` |
| Link two graph nodes | `loregarden_create_memory_relation` | Inserts `memory_relations` row |
| Confirm all backends | `loregarden_memory_status` | — |

MCP write responses include `obsidian` and/or `graph` blocks. Use `graph.id` from upsert responses as `source_id` / `target_id` for relations.

## Search results

`loregarden_search_memory` returns:

- `obsidian` — markdown note hits (`source: "obsidian"`)
- `graph` — SQLite node hits (`source: "sqlite"`, includes `node_type`)

Check both arrays before writing duplicate nodes.

## Rules

1. **MCP only** — no direct file writes, no `sqlite3` CLI, no SQL against `memory_sqlite_path`.
2. **Always scope by workspace** — pass `workspace_slug` on every memory call.
3. **Right tool, right backend** — blog posts never go to SQLite; relations never go to Obsidian.
4. **Learning Agent** — `append_learning` + `upsert_memory` dual-write; link with `create_memory_relation` using graph node ids.
5. **Blog Post Agent** — `upsert_blog_post` → Obsidian only.
6. **No fabrication** — if `memory_sqlite_path` is null and Obsidian is disabled, report it; do not invent storage.

## macOS Obsidian sync note

Obsidian vaults synced via iCloud live under `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/`. The memory graph SQLite often lives under the same vault or iCloud `Loregarden/` tree — use `loregarden_memory_status` for the resolved path. SQLite in iCloud uses DELETE journal mode to avoid sync conflicts.
