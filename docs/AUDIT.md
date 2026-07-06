# Loregarden — Codebase Audit

**Date:** 2026-07-06
**Scope:** Full codebase — `server/` (FastAPI control plane), `client/` (React IDE), `agent_context/`, scripts, config.
**Type:** Findings & recommendations. No code changed by this audit.

---

## 1. Executive summary

Loregarden is an "Agent SDLC IDE": a local control plane (FastAPI + SQLModel/SQLite) that orchestrates multi-agent coding workflows by spawning agent CLIs (Claude, Cursor, Codex, LM Studio, or a local stub) against git workspaces, driven by a React 19 + TypeScript IDE. It is roughly **29K LOC of application code** — ~14.7K Python (server), ~14.4K TS/TSX (client) — plus a strong ~9.5K-LOC Python test suite and a rich YAML/markdown agent-orchestration layer.

The project is well-organized, feature-complete (no half-built stubs or `TODO` debt), cleanly configured, and commits no secrets. Its backend test suite is a genuine strength, including adversarial suites. The weaknesses are **cross-cutting infrastructure and a broad, unauthenticated local attack surface** rather than scattered bugs.

**Top 5 findings:**

| # | Finding | Severity |
|---|---------|----------|
| 1 | No authentication on any endpoint, over an API that writes files and executes local agent processes | High |
| 2 | Browse/import ceiling is the entire home directory (`~`), far wider than the "workspace" framing implies | High |
| 3 | No CI/CD at all; no Python lint/type gates; TypeScript strict mode off | Medium |
| 4 | God files (`Dashboard.tsx` 2100 lines, `StudioPage.tsx`, `client.ts`, `domain.py`) and hand-rolled unversioned DB migrations | Medium |
| 5 | No committed backend lockfile (`uv.lock` gitignored) → non-reproducible installs | Medium |

---

## 2. Scorecard

| Dimension | Rating | One-line rationale |
|-----------|--------|--------------------|
| Security | ⚠️ Needs work | No auth; home-dir-wide file access; permission-bypass modes. No injection surface, no committed secrets. |
| Architecture | 🟡 Adequate | Clean layering and service split, but several 800–2100-line god files and ad-hoc migrations. |
| Backend testing | ✅ Strong | ~43 test files incl. adversarial suites, broad coverage across services/API/workflows. |
| Frontend testing | ⚠️ Needs work | ~11 test files for ~51 sources; only 2–3 of ~28 components tested. |
| CI/CD | ⚠️ Needs work | None. Nothing gates merges; all checks are manual local scripts. |
| Tooling (lint/types) | ⚠️ Needs work | No Python linter/formatter/type-checker; TS strict off; oxlint minimal. |
| Dependencies | 🟡 Adequate | Client lockfile committed; backend lockfile gitignored; unbounded `>=` floors. |
| Documentation | 🟡 Adequate | Strong README + agent docs; no CONTRIBUTING/ARCHITECTURE/CHANGELOG. |
| Error handling | 🟡 Adequate | Consistent API error mapping; several silently-swallowed errors in security-sensitive paths. |

---

## 3. Findings by dimension

### 3.1 Security  *(highest priority)*

**H-1 — No authentication on any endpoint.**
Every route's only dependency is `Depends(get_session)` (a DB session, not an identity). There is no API key, token, cookie, or auth middleware anywhere. CORS is locked to `localhost:5173` (`server/loregarden/main.py:25-31`), but CORS only constrains browsers — the API is reachable by **any local process** on `127.0.0.1:8000`. Because the API writes files and spawns agent subprocesses, "any local process can drive it" is a real capability, not a theoretical one.

**H-2 — Home-directory-wide browse/read ceiling.**
`server/loregarden/services/path_browser.py:11` sets `BROWSE_CEILING = Path.home().resolve()`. The browse and import endpoints (`read_import_files`) can therefore enumerate directories and read `.md/.json/.yaml/.yml` files anywhere under `~`, not just inside a workspace. A workspace `repo_path` can also be pointed anywhere under home, after which the editor's read/write applies there too. Combined with H-1, this exposes far more of the filesystem than the "workspace" framing suggests.

**M-1 — Unauthenticated file write.**
`PUT /api/.../editor/file` (`server/loregarden/api/editor.py` → `services/file_editor.py`) writes files. Traversal *is* defended within a workspace — `resolve_editor_file` rejects absolute paths and `..` (`file_editor.py:151`), enforces containment via a resolved-prefix check (`:155`), a text-suffix allowlist, and a 512 KB cap. The residual risk is authorization, not traversal: any local caller can overwrite source in any registered workspace.

**M-2 — Permission-bypass modes disable human-in-the-loop.**
`permission_bypass_enabled()` (`server/loregarden/agents/cli_adapters.py:53`) gates Claude's `--permission-mode bypassPermissions` (`:61`) and Cursor's `--trust --force` (`:124`, `:183`) behind `LOREGARDEN_ALLOW_PERMISSION_BYPASS`. It defaults to `false`, but when set it hands agents unrestricted tool use. More notably, **triage always runs with bypass** regardless of the flag — Claude `bypassPermissions` (`:367`) and Cursor `--trust --force` (`:387`). Worth documenting loudly and guarding against non-dev use.

**M-3 — OAuth tokens flow through the unauthenticated usage API.**
`services/usage_service.py:157` reads Claude credentials from the macOS keychain (`security find-generic-password`), `~/.claude`, and env vars, refreshes via `platform.claude.com`, and sends `Authorization: Bearer` to `api.anthropic.com`. Tokens are handled in-memory and not obviously logged, but they are surfaced through an endpoint with no auth — confirm no usage response echoes the token back.

**Positives (security done right):**
- No `shell=True`, `os.system`, `eval`, or `exec` — all subprocess calls use argv lists, so there is no classic shell-injection surface. `shlex.split` is used only where safe.
- No secrets committed; `.env`/`.envrc` are gitignored. Grep hits for "secret/token" are benign (`secrets.token_hex()` for run IDs, LM Studio "tokens", usage counters).
- Env-driven command override (`cli_adapters.py`) is operator config, not request input.

### 3.2 Architecture & code quality

**M-4 — God files.** A few single files concentrate too much:
- `client/src/pages/Dashboard.tsx` — **2100 lines** (the main view).
- `client/src/pages/StudioPage.tsx` — 1089 lines.
- `client/src/api/client.ts` — 806 lines, mixing every API call with all shared type definitions.
- `server/loregarden/models/domain.py` — 806 lines, mixing ~14 ORM `table=True` models with ~40 request/view DTOs.

These are the main maintainability risks — large blast radius, hard to test in isolation, merge-conflict magnets.

**M-5 — Hand-rolled, unversioned SQLite migrations.**
`server/loregarden/db/session.py:54` `_apply_sqlite_migrations` is a chain of `PRAGMA table_info` + ad-hoc `ALTER TABLE ADD COLUMN` with no version tracking and no Alembic. It works today but is fragile: no rollback, no ordering guarantees, silent no-op on drift, and it grows unbounded with every schema change.

**L-1 — Duplicated logic.**
- Per-adapter argv construction is largely duplicated between `resolve_cli_invocation` and `build_triage_invocation` in `agents/cli_adapters.py`.
- The subprocess spawn/stream loop is repeated across `agents/executors/cli.py`, `services/triage_service.py`, and `agents/executors/permission_bridge.py`.

**L-2 — Dead / stub code in production paths.**
- `client/src/api/client.ts:19` `isWorkflowWorkItem()` always returns `true` — a no-op gate; callers can't actually branch on it.
- `services/triage_service.py:185` returns `LOREGARDEN_TRIAGE_STUB_RESPONSE` verbatim if the env var is set — a test seam living in the request path.
- `config.py:14` (and `api/workflows.py:21`) use inline `__import__("os"/"json")` instead of top-level imports — a minor smell.

### 3.3 Testing & CI/CD

**M-6 — No CI/CD whatsoever.** There is no `.github/workflows`, GitLab CI, or any pipeline. Tests, lint, and build run only via local scripts (`scripts/test-server.sh`, `client` npm scripts). Nothing is gated on merge, so regressions and lint drift can land freely.

**M-7 — Thin frontend test coverage.** ~11 client test files vs ~51 sources (~0.2 ratio). `src/lib/` utilities are well covered (slugify, pathExplorer, hierarchy, stageDisplay, importTicketPreview), but only 2–3 of ~28 components have tests (`TicketDetailsModal`, `DashboardTicketDetailsButton`). Pages, the API client, and Zustand state are untested. *(By contrast the backend suite is strong — see positives.)*

**L-3 — No coverage enforcement; stray test-doc files.** No coverage tooling is wired (only `.coverage`/`htmlcov` gitignored). Test-design markdown is committed alongside tests, e.g. `server/tests/TEST_DESIGN_25_ANALYSIS.md`, `client/src/components/__tests__/ADVERSARIAL_TEST_SUMMARY.md` — clutter that probably belongs in `docs/` or nowhere.

**Positive:** The backend suite (~43 files) is broad and includes explicit adversarial suites (`test_workflow_deep_adversarial.py`, `test_workflow_any_ticket_adversarial.py`) covering API, services, orchestration, workflows, MCP, permissions, triage, imports, and git branching.

### 3.4 Tooling: lint, format, types

**M-8 — No Python linting/formatting/type-checking.** `server/pyproject.toml` declares no ruff, black, flake8, isort, or mypy/pyright. Pydantic/SQLModel give runtime validation, but there is no static gate.

**M-9 — TypeScript strict mode off.** No `"strict": true` in any tsconfig (`client/tsconfig.app.json`); only partial flags are on (`noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`), and `skipLibCheck` is true. oxlint enables only two rules (`client/.oxlintrc.json`). `tsc -b` runs during `build` but not as a standalone gate.

### 3.5 Dependencies & reproducibility

**M-10 — No committed backend lockfile.** `server/uv.lock` is gitignored, so every `uv sync` can resolve differently — non-reproducible backend installs. Runtime deps use unbounded `>=` floors with no upper bounds. There is no dependency scanning (no CI to host it).

**Positive:** `client/package-lock.json` is committed; client deps are modern with a small security surface (`monaco-editor`, fetch-based API; no auth/crypto libs).

### 3.6 Documentation

**L-4 — Missing standard project docs.** `README.md` is strong (architecture table, prerequisites, quick-start, env-var reference, layout, workflow overview), and `agent_context/` is richly documented. But there is no `CONTRIBUTING`, `CHANGELOG`, or `ARCHITECTURE.md`; `docs/` currently holds only design-reference HTML. Inline docstrings are sparse-to-moderate.

### 3.7 Error handling & robustness

**Consistent where it counts:** API routers uniformly map `ValueError → HTTPException(400, str(exc))`, and subprocess handling (timeouts, `proc.kill()`, status mapping) is solid across `cli.py`, `triage_service.py`, and `gate_runner.py`.

**L-5 — Silently swallowed errors, several in sensitive paths:**
- `services/path_browser.py:110,155` — per-entry `OSError` swallowed in `os.scandir` loops; `normalize_browse_target` falls back to repo root on most `ValueError`s, masking misconfiguration.
- `services/gate_runner.py:56` — `format_gate_command` swallows `KeyError` and returns the unformatted template, so a broken gate command can run with literal `{placeholders}`.
- `services/usage_service.py` — ~8 broad excepts; token/usage failures degrade silently to no data.
- `agents/executors/permission_bridge.py` (855 lines) — ~10 broad/timeout excepts in the security-critical approval path; deserves the closest read.

**L-6 — Internal path leakage.** API error messages surface absolute filesystem paths (e.g. from `file_editor` errors) to the client. Low impact given the local-only framing, but worth sanitizing.

---

## 4. Prioritized recommendations

Advisory only — nothing here was implemented by this audit.

### Quick wins (low effort, high value)

| Rec | Addresses | Effort |
|-----|-----------|--------|
| Add a GitHub Actions workflow running `pytest`, `jest`, oxlint, and `tsc --noEmit` on PRs | M-6 | S |
| Commit `server/uv.lock` (remove from `.gitignore`) for reproducible installs | M-10 | XS |
| Add ruff (lint + format) to the server; wire into the same CI job | M-8 | S |
| Enable TypeScript `strict` and fix the fallout incrementally | M-9 | S–M |
| Delete the `isWorkflowWorkItem` no-op or restore its intended gating; move test seams out of the request path | L-2 | XS |
| Narrow `BROWSE_CEILING` to registered workspace roots (or a configurable allowlist) | H-2 | S |

### Larger efforts

| Rec | Addresses | Effort |
|-----|-----------|--------|
| Add a minimal auth layer (shared-secret/localhost token) even for local use, at least for file-write and run-trigger endpoints | H-1, M-1 | M |
| Confirm usage responses never echo OAuth tokens; add a regression test | M-3 | S |
| Adopt Alembic (or a versioned migration table) to replace hand-rolled `_apply_sqlite_migrations` | M-5 | M |
| Split god files: extract Dashboard/Studio sub-views, split `client.ts` types from calls, split ORM tables from DTOs in `domain.py` | M-4 | M–L |
| Raise frontend component/page coverage toward the backend's bar | M-7 | M |
| Factor shared subprocess spawn/stream + per-adapter argv into one helper | L-1 | M |
| Tighten broad excepts in `permission_bridge.py`/`usage_service.py`; log instead of silently degrading | L-5 | M |

---

## 5. What's already good

- **Clean layering:** clear `api / services / agents / models / core` split on the server; distinct UI-state (Zustand) vs server-state (React Query) on the client.
- **Strong, adversarial backend test suite** — the healthiest part of the repo.
- **No injection surface:** argv-only subprocess invocation; no `shell=True`/`eval`/`exec`.
- **No committed secrets;** centralized, documented config via pydantic-settings (`LOREGARDEN_` prefix).
- **Feature-complete:** effectively no `TODO`/`FIXME`/`WIP` debt in application code; concerns are architectural, not abandoned.
- **Good top-level docs:** README and `agent_context/` are above average.

---

*Ratings key — severity: High (fix soon) / Medium (plan for) / Low (cleanup). Effort: XS (<1h) / S (hours) / M (a day-ish) / L (multi-day).*
