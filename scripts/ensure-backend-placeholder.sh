#!/usr/bin/env bash
# tauri-build validates `bundle.externalBin` resource paths at compile time
# for every build profile, including `tauri dev` — so a sidecar-named file
# has to exist on disk before Rust will even type-check, not just for
# `tauri build`. Dev mode never executes it (backend::spawn_dev runs `uv run`
# directly), so a cheap placeholder is enough; `npm run build:backend`
# overwrites it with the real PyInstaller binary when packaging.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/src-tauri/binaries"

if ! command -v rustc >/dev/null 2>&1; then
  echo "rustc is required to determine the sidecar target triple." >&2
  exit 1
fi

TARGET_TRIPLE="$(rustc -vV | awk '/^host:/ { print $2 }')"
EXT=""
[[ "$TARGET_TRIPLE" == *windows* ]] && EXT=".exe"
DEST="$OUT_DIR/loregarden-backend-${TARGET_TRIPLE}${EXT}"

mkdir -p "$OUT_DIR"
if [[ ! -f "$DEST" ]]; then
  echo "Creating placeholder backend sidecar for dev builds: $DEST"
  printf '#!/bin/sh\necho "dev placeholder sidecar — tauri dev runs the backend via uv, not this file" >&2\nexit 1\n' > "$DEST"
  chmod +x "$DEST"
fi
