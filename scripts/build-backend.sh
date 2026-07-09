#!/usr/bin/env bash
# Builds server/ into a single standalone executable and drops it into
# src-tauri/binaries/ named to match Tauri's sidecar convention
# (<name>-<target-triple>[.exe]), so `tauri build` can bundle it as an
# externalBin and the packaged app needs neither Python nor uv installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIDECAR_NAME="loregarden-backend"
OUT_DIR="$ROOT/src-tauri/binaries"
WORK_DIR="$ROOT/server/.pyinstaller"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi
if ! command -v rustc >/dev/null 2>&1; then
  echo "rustc is required to determine the sidecar target triple." >&2
  exit 1
fi

TARGET_TRIPLE="$(rustc -vV | awk '/^host:/ { print $2 }')"
if [[ -z "$TARGET_TRIPLE" ]]; then
  echo "Could not determine target triple from 'rustc -vV'." >&2
  exit 1
fi

EXT=""
[[ "$TARGET_TRIPLE" == *windows* ]] && EXT=".exe"
DEST="$OUT_DIR/${SIDECAR_NAME}-${TARGET_TRIPLE}${EXT}"

echo "Building backend sidecar for $TARGET_TRIPLE -> $DEST"

cd "$ROOT/server"
uv sync --group build

rm -rf "$WORK_DIR"
mkdir -p "$OUT_DIR" "$WORK_DIR"

# --onefile keeps the sidecar to a single binary. --collect-submodules
# loregarden is required because __main__.py hands uvicorn.run() the app as
# the string "loregarden.main:app" (resolved at runtime by uvicorn's own
# importer) rather than a literal `import loregarden.main` — PyInstaller's
# static analysis never sees that string, so without this flag it silently
# ships a binary that raises ModuleNotFoundError('loregarden') on boot. The
# uvicorn hidden-imports below are the standard, separate boilerplate for its
# protocol/loop backends, which are also selected dynamically.
uv run pyinstaller \
  --onefile \
  --name "$SIDECAR_NAME" \
  --distpath "$WORK_DIR/dist" \
  --workpath "$WORK_DIR/build" \
  --specpath "$WORK_DIR" \
  --collect-submodules loregarden \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols \
  --hidden-import uvicorn.protocols.http \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan \
  --hidden-import uvicorn.lifespan.on \
  loregarden/__main__.py

BUILT_BIN="$WORK_DIR/dist/${SIDECAR_NAME}${EXT}"
if [[ ! -f "$BUILT_BIN" ]]; then
  echo "PyInstaller did not produce the expected binary at $BUILT_BIN" >&2
  exit 1
fi

mv "$BUILT_BIN" "$DEST"
chmod +x "$DEST"
echo "Backend sidecar ready: $DEST"
