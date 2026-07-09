# Desktop app (Tauri)

Loregarden's desktop app is a thin native host around the *existing* React frontend (`client/`) and FastAPI backend (`server/`) — neither was rewritten or restructured to support it. Tauri owns process lifecycle, window creation, and native OS integration; all business logic stays in Python and all UI stays in React, talking to each other over the same HTTP API they already use in the browser.

## Layout

```
src-tauri/
├── Cargo.toml
├── tauri.conf.json          # window, build commands, bundling, CSP
├── capabilities/default.json # least-privilege webview permissions
├── config/app.config.json   # backend port/timeouts/log level — compiled in via include_str!
├── icons/                   # placeholder icon set — replace before shipping
├── binaries/                # PyInstaller sidecar output (gitignored, built on demand)
└── src/
    ├── main.rs              # setup: spawn backend, wait for readiness, show window / error dialog; exit: graceful shutdown
    ├── config.rs             # AppConfig, loaded from the bundled JSON + env overrides
    └── backend.rs             # dev/prod spawn, TCP readiness poll, SIGTERM→SIGKILL shutdown, app-data seeding
```

`server/loregarden/__main__.py` (`python -m loregarden [--reload] [--host] [--port] [--log-level]`) is the single entrypoint used both by Rust in dev (via `uv run`) and as the PyInstaller build target for the packaged sidecar — one code path instead of dev/prod command strings drifting apart.

## Running it

```bash
npm install          # once, at the repo root — installs @tauri-apps/cli
npm run tauri:dev
```

This runs `tauri dev`, which:
1. Runs `beforeDevCommand` (`npm --prefix client run dev`) to start the Vite dev server and waits for `http://localhost:5173` to respond.
2. Launches the Rust shell, whose `setup` hook spawns `uv run python -m loregarden --reload --host 127.0.0.1 --port 8000` in `server/` (same command shape as `scripts/dev-server.sh`) and polls the port until it accepts connections.
3. Shows the window pointed at the Vite dev URL once the backend is ready — or shows a native error dialog and exits if it never comes up within `startupTimeoutSecs`.
4. On quit, sends the backend `SIGTERM`, waits up to `shutdownGraceSecs`, then force-kills it if it hasn't exited — no zombie `uvicorn` processes left behind. Three independent layers cover this, each closing a gap the others don't:
   - **`RunEvent::Exit`** in `main.rs` — the reliable, always-fires-on-cooperative-shutdown hook. Note this is deliberately `RunEvent::Exit`, not the more commonly-reached-for `RunEvent::ExitRequested`: on macOS, `ExitRequested` does **not** fire for Cmd+Q, the Dock, or the app menu's Quit — only for closing the window via its titlebar button or an explicit `app_handle.exit()` call. This is a confirmed, still-open upstream bug ([tauri-apps/tauri#9198](https://github.com/tauri-apps/tauri/issues/9198)); verified here by testing an actual packaged build against a real quit action, not just compiling against the "obvious" API. `RunEvent::Exit` is the community-verified fix and is what this code uses; `ExitRequested` is also still handled (idempotently) for the paths where it does work.
   - **A `signal-hook` listener** on Unix — catches a signal sent straight to the app process (`kill`, a session manager, CI teardown) that bypasses tao's event loop entirely.
   - **A parent-liveness watchdog in `server/loregarden/__main__.py`** — the backend itself polls whether the PID it was told about via `LOREGARDEN_PARENT_PID` is still alive, and hard-exits if not. This is the backstop for the case neither Rust-side mechanism can cover: the parent crashing, getting SIGKILLed, or any exit path that never runs Rust cleanup code at all. It checks the specific PID rather than `os.getppid()` because PyInstaller's `--onefile` bootloader forks a child to run the actual Python code — that child's real OS parent is the bootloader, not the Tauri app that ultimately launched it, so `getppid()` never changes even after the true parent is long gone.

Editing `client/src/**` hot-reloads inside the Tauri window exactly like it does in a browser tab, because it *is* the same Vite dev server. Editing `server/**` still triggers uvicorn's `--reload`.

`tauri-build`'s `build.rs` validates that `bundle.externalBin` resource files exist on disk at compile time, for every profile — including plain `cargo check`/`tauri dev`, which never actually execute the sidecar. A `pretauri:dev` npm hook (`scripts/ensure-backend-placeholder.sh`) creates a trivial placeholder at `src-tauri/binaries/loregarden-backend-<target-triple>` the first time you run `npm run tauri:dev` on a machine, so a fresh clone doesn't need a full PyInstaller build just to type-check. `npm run build:backend` overwrites it with the real binary when you package.

The plain browser flow (`./scripts/dev-server.sh` + `cd client && npm run dev`) is untouched and keeps working — Tauri is additive.

## Building a distributable

```bash
npm run tauri:build
```

This runs `scripts/build-backend.sh` (packages `server/` into a standalone executable with PyInstaller, named `loregarden-backend-<target-triple>` to match Tauri's [sidecar convention](https://tauri.app/develop/sidecar/)) and then `tauri build`, which compiles the Rust shell in release mode, bundles the sidecar as an `externalBin`, and produces a platform installer under `src-tauri/target/release/bundle/`. The end user needs neither Python nor `uv` — the sidecar is a self-contained binary.

## Why the app-data seeding step

The backend isn't just Python code — it reads `agent_context/` (agent/workflow definitions) and writes `data/loregarden.db` + `project_board/` relative to `LOREGARDEN_REPO_ROOT`. A packaged app has no "repo checkout" on the end user's machine, so:

- `agent_context/` is bundled as a **read-only Tauri resource** (`bundle.resources` in `tauri.conf.json`).
- On first launch in a release build, `backend::ensure_app_data_seeded` copies it into the OS per-user app-data directory (e.g. `~/Library/Application Support/com.loregarden.desktop` on macOS) if it isn't already there, and creates empty `data/` and `project_board/` directories alongside it.
- `LOREGARDEN_REPO_ROOT` is set to that app-data directory before the sidecar is spawned, so the *existing* config resolution in `server/loregarden/config.py` needs no changes.
- Dev mode skips all of this and points `LOREGARDEN_REPO_ROOT` straight at the real monorepo checkout, matching today's `dev-server.sh` behavior.

This is chosen deliberately over the simpler alternative (packaged app still expects `LOREGARDEN_REPO_ROOT` to point at an external git checkout) so the app is genuinely double-click-and-go for someone who has never cloned this repo.

## Configuration

All backend lifecycle knobs live in `src-tauri/config/app.config.json` (compiled into the binary, so behavior is identical between `cargo run` and the packaged app):

| Key | Default | Meaning |
|---|---|---|
| `backend.host` | `127.0.0.1` | Bind address |
| `backend.port` | `8000` | Bind port — must match `client/src/api/client.ts`'s `API_BASE` default |
| `backend.startupTimeoutSecs` | `30` | How long to wait for the port before showing the error dialog |
| `backend.pollIntervalMs` | `200` | Readiness poll interval |
| `backend.shutdownGraceSecs` | `5` | Grace period between SIGTERM and SIGKILL |
| `backend.logLevel` | `info` | Passed through to uvicorn |
| `backend.sidecarName` | `loregarden-backend` | Must match `bundle.externalBin` and `scripts/build-backend.sh`'s output name |

Each has a matching env var override (`LOREGARDEN_DESKTOP_PORT`, etc. — see `src/config.rs`) for local tweaking without a rebuild.

## Security

`capabilities/default.json` grants the webview exactly: `dialog:allow-open`/`allow-save`, `clipboard-manager:allow-read-text`/`allow-write-text`, the three `notification:allow-*` permissions needed to request-and-send, and `opener:allow-default-urls` (opens `http(s)://`/`mailto:`/`tel:` in the OS default app — not arbitrary local paths). There is **no filesystem plugin and no shell-execute permission exposed to the webview** — the backend sidecar is only ever spawned from trusted Rust setup code via `tauri-plugin-shell`'s Rust API, which capabilities don't gate (they only gate what the frontend can `invoke()` over IPC).

The window's CSP (`app.security.csp` in `tauri.conf.json`) restricts script/style/font/connect sources to `'self'` plus exactly what `client/index.html` and `client/src/api/client.ts` actually use (Google Fonts, `http(s)://127.0.0.1:*` for the local API).

## Platform abstraction (frontend)

`client/src/services/platform/` is the only place the React app touches Tauri APIs. It exports a single `platform: PlatformAdapter` (`openFile`, `saveFile`, `notify`, `clipboardRead`/`clipboardWrite`, `openExternal`), backed by `tauri.ts` or `web.ts` depending on a `'__TAURI_INTERNALS__' in window` check done once at module load. Everything else in the app imports `platform` from `client/src/services/platform` and never branches on environment itself. `openFile`/`saveFile` are native path pickers only — actual file reads/writes still go through the existing FastAPI editor/browse endpoints, so the browser build (where a real filesystem path can't be returned for security reasons) degrades to a documented no-op with a console warning rather than a half-working substitute.

## Native features

Configured but intentionally minimal, per the plugin list above: native open/save dialogs, clipboard read/write, notifications, and opening external links in the OS default browser. Drag-and-drop is enabled by Tauri's window defaults (no plugin required) but no UI drop handler has been wired up yet — that's future work, not something this integration needed. The window is created with an explicit `label: "main"` and built via the standard `WebviewWindowBuilder` path so adding a second window later doesn't require restructuring anything here.

## Known limitations

- **Windows graceful shutdown**: `SIGTERM` has no real equivalent for an arbitrary child process on Windows. `backend::terminate` sends it on Unix and waits for a graceful exit (uvicorn already handles `SIGTERM` cleanly); on Windows it force-kills immediately. This is a documented platform limitation, not a bug — implementing a real ctrl-break-based graceful path would add meaningfully more Windows-specific process-group code for a rare case (this app currently only writes to a local SQLite DB per request, so an abrupt kill has limited blast radius).
- **Icons**: `src-tauri/icons/` currently holds Tauri's default placeholder icon set (generated via `tauri icon`). Replace it with real branding (ideally from a ≥1024×1024 source image) before distributing a build.
- **PyInstaller onefile startup cost**: `--onefile` binaries self-extract to a temp directory on each launch, adding a small startup delay. If that becomes noticeable, switch `scripts/build-backend.sh` to `--onedir` and update `bundle.resources`/`externalBin` accordingly — everything else in this design is agnostic to which PyInstaller mode is used.
- **Rosetta 2 first launch, on an Intel Rust/Python toolchain running on Apple Silicon**: if `rustc`/`uv`'s Python were installed as x86_64 builds (e.g. Homebrew under `/usr/local` instead of the native `/opt/homebrew` prefix), the sidecar binary is x86_64 too and macOS translates it via Rosetta. The *first* launch after a fresh build pays a one-time cost to AOT-translate every shared library the PyInstaller bundle loads (observed here: ~25s for a cold run); it's cached under `/private/var/db/oah/` after that and subsequent launches are fast. `startupTimeoutSecs` defaults to 45s specifically to give this room — if you see the startup-failed dialog on a fresh machine, check `rustc -vV`'s `host:` line and consider installing native-arch toolchains instead of raising the timeout further.
- **`scripts/build-backend.sh`'s `--collect-submodules loregarden` flag is load-bearing, not boilerplate**: `server/loregarden/__main__.py` hands uvicorn the app as the string `"loregarden.main:app"`, resolved at runtime by uvicorn's own importer rather than a literal `import` statement. PyInstaller's static analysis can't see through that string, so without this flag the sidecar builds successfully but crashes on boot with `ModuleNotFoundError: No module named 'loregarden'` — it never even attempted to bundle the app's own code. Caught by actually running the built binary standalone during verification, not by the build succeeding.
- **`tauri build`'s frontend step uses `npm run build:vite-only` (`vite build` alone), not `npm run build` (`tsc -b && vite build`)**: `client`'s full `tsc -b` project-wide type-check already fails on `main` independent of this integration — ~15 pre-existing errors across `Dashboard.tsx`, `services/websocket.ts`, `lib/useAppNavigation.ts`, `state/uiStore.ts`, missing `@types/node` coverage for a few files, and a stale test-vs-`TicketDetail`-type mismatch. None of that is Tauri-related, and fixing it is out of scope here. `vite build` produces the same working `client/dist` output via esbuild (which strips types without fully type-checking, same as what `npm run dev` already does) without depending on that unrelated, already-broken gate. One real bug *was* fixed as part of this change: `ParallelQueueVisualization.integration.test.ts` contained JSX but had a `.ts` extension, which `tsc` rejects outright — renamed to `.tsx`. Worth cleaning up the rest of the `tsc -b` errors separately; `npm run build` in `client/` will tell you exactly where things stand.
