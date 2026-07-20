import type { WorkspaceRuntimeSettings } from "../api/client";

/** Runtime a conversation falls back to before the server reports its own. */
export const DEFAULT_RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};
