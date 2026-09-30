---
description: Loregarden memory protocol — GRAPH record + vault export for durable knowledge; vault-native blog/checkpoints.
globs: []
alwaysApply: true
---
# MEMORY PROTOCOL

## Authority (record vs export)

| Kind | Authoritative store | Vault role |
|------|---------------------|------------|
| **Memory** / **Learning** | **GRAPH** (`memory_nodes` at `memory_sqlite_path`) | One-way **export** (`derived: true`, shared `id`) — not a second record |
| **Relations** | **GRAPH** (`memory_relations`) | None (never in the vault) |
| **Blog post** | **VAULT** only | Native record; not agent memory |
| **Checkpoint** | **VAULT** only | Per-run append log; not agent memory |
| FTS / embeddings / briefing caches | Disposable over GRAPH | Rebuild anytime; never the source of a claim |

Hand-edits in Obsidian for memory/learnings do **not** flow back into the record.
Operator edits go through loregarden (MCP/UI), which update the graph and re-export.
Durable nodes stay in the iCloud SQLite at `memory_sqlite_path` — **not** `data/loregarden.db`.

Agents must **never** write Obsidian files or SQLite databases directly — always use Loregarden MCP tools.

## Kind → tool map

| Artifact | MCP tool | Record | Export / notes |
|----------|----------|--------|----------------|
| **Memory** | `loregarden_upsert_memory` | GRAPH `node_type: memory` | Vault markdown export when configured; `obsidian.id == graph.id` |
| **Learnings** | `loregarden_append_learning` | GRAPH `node_type: learning` | Same shared-id export |
| **Blog posts** | `loregarden_upsert_blog_post` | VAULT | Never in SQLite |
| **Checkpoints** | `loregarden_append_checkpoint` | VAULT | `{ticket_id}/{run_id}.md` append log |
| **Relations** | `loregarden_create_memory_relation` | GRAPH | Use `graph.id` from write responses |

Default Obsidian subdirs: `Loregarden/Memory`, `Loregarden/Learnings`, `Loregarden/BlogPosts`, `Loregarden/Checkpoints`.

Default SQLite layout: base `…/Loregarden/memory.db` → per workspace `…/Loregarden/{workspace_slug}/memory.db`.

## Discover paths (first call)

Call `loregarden_memory_status` with the run's `workspace_slug`:

```json
{"workspace_slug": "loregarden"}
```

| Field | Meaning |
|-------|---------|
| `obsidian_vault` | Root Obsidian vault |
| `obsidian_memory_dir` | Durable memory markdown **exports** |
| `obsidian_learnings_dir` | Learning markdown **exports** |
| `obsidian_blogposts_dir` | Blog post markdown (vault-native) |
| `obsidian_checkpoints_dir` | Checkpoint log markdown (vault-native) |
| `memory_sqlite_path` | Per-workspace graph DB file path (**the record**) |
| `memory_sqlite_url` | Same DB as `sqlite:///` URL (operators) |
| `memory_graph_tables` | `memory_nodes`, `memory_relations` |
| `memory_graph_node_types` | `memory`, `learning` |
| `memory_graph_excludes` | `blog_post`, `checkpoint` — never in SQLite |

`database_path` in the same response is the **Loregarden control-plane** DB (tickets, runs) — not agent memory. Do not confuse it with `memory_sqlite_path`.

## Required identifier

Every memory tool call must include `workspace_slug` from the run prompt.

## Which tool to use

| Goal | Tool | Side effect |
|------|------|-------------|
| Ticket learnings | `loregarden_append_learning` | GRAPH first, then labelled vault export |
| Durable patterns / anti-patterns | `loregarden_upsert_memory` | GRAPH first, then labelled vault export |
| Human-readable blog post | `loregarden_upsert_blog_post` | VAULT only |
| Per-run assumption/ambiguity log | `loregarden_append_checkpoint` | VAULT only |
| Find prior context | `loregarden_search_memory` | Graph: memory\|learning; Obsidian: blog\|checkpoint |
| Link two graph nodes | `loregarden_create_memory_relation` | `memory_relations` row |
| Confirm all backends | `loregarden_memory_status` | — |

MCP write responses include `graph` (required for memory/learning) and optional `obsidian` export. Use `graph.id` as `source_id` / `target_id` for relations.

## Search results

`loregarden_search_memory` returns:

- `obsidian` — vault-native hits only (`blog_post`, `checkpoint`); memory/learning exports are not peer hits
- `graph` — SQLite durable hits (`memory`, `learning`)

Check `graph` before writing duplicate durable nodes. Blog/checkpoint duplicates live in the vault arrays only.

## Rules

1. **MCP only** — no direct file writes, no `sqlite3` CLI, no SQL against `memory_sqlite_path`.
2. **Always scope by workspace** — pass `workspace_slug` on every memory call.
3. **Right tool, right backend** — blog posts never go to SQLite; relations never go to Obsidian; memory/learning require GRAPH (fail closed without it).
4. **Learning Agent** — `append_learning` + `upsert_memory` write the graph record then export; link with `create_memory_relation` using graph node ids.
5. **Blog Post Agent** — `upsert_blog_post` → Obsidian only.
6. **No fabrication** — if `memory_sqlite_path` is null, report it; do not invent durable storage in the vault alone.

## macOS Obsidian sync note

Obsidian vaults synced via iCloud live under `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/`. The memory graph SQLite often lives under the same vault or iCloud `Loregarden/` tree — use `loregarden_memory_status` for the resolved path. SQLite in iCloud uses DELETE journal mode to avoid sync conflicts.
