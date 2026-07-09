import { tauriPlatform } from "./tauri";
import { webPlatform } from "./web";
import type { PlatformAdapter } from "./types";

export type { FilePickerOptions, NotifyOptions, PlatformAdapter } from "./types";

// Tauri v2 injects this marker into the webview's `window` before any app
// code runs — the one place in the app that knows which host it's in.
function isRunningInTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * The rest of the app should only ever talk to `platform`, never import
 * `@tauri-apps/*` or browser-only APIs directly — that keeps the browser
 * build working unmodified and the desktop wiring in one place.
 */
export const platform: PlatformAdapter = isRunningInTauri() ? tauriPlatform : webPlatform;
