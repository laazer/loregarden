# Loregarden

Loregarden is an Agent SDLC IDE — a local control plane for orchestrating multi-agent software development workflows. It tracks work as tickets in SQLite, runs agents through configurable TDD pipelines, surfaces approval prompts in an inbox, and exposes an MCP endpoint so external agents can participate in the same workflow.

## Architecture

| Layer | Stack | Role |
|-------|-------|------|
| **Control plane** | FastAPI + SQLModel + SQLite | Tickets, runs, workflows, approvals, MCP |
| **IDE shell** | React + TypeScript + Vite | Dashboard, studio, triage, approval inbox |
| **Agent runtime** | CLI adapters (local, Claude, Cursor, LM Studio) | Executes pipeline stages with permission bridging |
| **Workflow config** | YAML + markdown in `agent_context/` | Agent prompts, pipeline stages, orchestration profiles |

Tickets are authoritative in the database (`data/loregarden.db`).

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package manager)
- Node.js 20+ and npm
- Python 3.10+
- [Rust](https://www.rust-lang.org/tools/install) — only needed for the desktop app (`npm run tauri:dev` / `npm run tauri:build`), not for the browser dev flow below

## Quick start

```bash
# Install backend deps and start the API (http://127.0.0.1:8000)
./scripts/dev-server.sh

# In another terminal — install frontend deps and start the IDE (http://localhost:5173)
cd client && npm install && npm run dev
# or: ./scripts/dev-client.sh
```

Initialize or reset the database:

```bash
./scripts/init-db.sh
```

Health check: `curl http://127.0.0.1:8000/health`

## Development

### Backend tests

```bash
./scripts/test-server.sh
```

### Frontend tests

```bash
cd client && npm test
```

### MCP server

Loregarden embeds MCP at `POST /mcp` on the main API server. For stdio-based MCP clients, use the proxy script:

```bash
./scripts/mcp-server.sh
```

## Desktop app

Loregarden also ships as a native desktop app via [Tauri](https://tauri.app) — a thin shell around the exact same React frontend and FastAPI backend described above; nothing in `client/` or `server/` changes to support it.

```bash
npm install                 # once, installs @tauri-apps/cli at the repo root
npm run tauri:dev           # starts FastAPI + Vite + opens the desktop window
```

`npm run tauri:build` produces a distributable installer (Python is bundled in, so end users don't need `uv`/Python installed). See [docs/tauri.md](docs/tauri.md) for how backend lifecycle management, configuration, and native capabilities are wired up.

## Project layout

```
loregarden/
├── agent_context/     # Agent prompts, workflows, orchestration profiles
├── client/            # React IDE shell
├── server/            # FastAPI control plane (loregarden package)
├── src-tauri/         # Desktop app shell (Rust) — see docs/tauri.md
├── scripts/           # Dev, test, and utility scripts
├── docs/design/       # UI design references
└── data/              # SQLite database (gitignored)
```

## Configuration

Environment variables use the `LOREGARDEN_` prefix. Common options:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOREGARDEN_REPO_ROOT` | auto-detected | Repository root path |
| `LOREGARDEN_DATABASE_URL` | `sqlite:///data/loregarden.db` | Ticket and run storage |
| `LOREGARDEN_CLI_ADAPTER` | `local` | Agent runner: `local`, `claude`, `cursor`, `lmstudio` |
| `LOREGARDEN_MCP_URL` | `http://127.0.0.1:8000/mcp` | MCP endpoint for agent tools |
| `LOREGARDEN_ALLOW_PERMISSION_BYPASS` | `false` | Dev-only escape hatch for permission prompts |

See `scripts/dev-server.sh` for the full list, including optional iCloud and Obsidian memory backends.

## Agent workflows

Multi-agent TDD pipelines (Planner → Spec → Test Designer → Implementers → QA → Review → Gatekeeper) are defined in `agent_context/`. The default Loregarden profile is `agent_context/orchestration/loregarden.yaml` with workflow template `loregarden-tdd`.

Agents should read `agent_context/agents/readme.md` for role definitions and workflow enforcement rules before acting on tickets.

## License

See [LICENSE](LICENSE).
