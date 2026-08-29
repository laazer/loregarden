/**
 * A shell in a container.
 *
 * `TerminalPanel` already owns exactly one xterm instance and one
 * websocket-backed shell, and takes nothing but a workspace slug — so a
 * container gets its own shell for free, and unmounting the container reaps it.
 * The tab strip and split controls in `TerminalWorkspace` are page chrome and
 * deliberately stay out of here: a container *is* the pane.
 */

import { TerminalPanel } from "../../TerminalPanel";
import { definePrimitive } from "./definePrimitive";
import { Unconfigured } from "./Unconfigured";

type TerminalSettings = {
  workspaceSlug: string;
};

export const terminalPrimitive = definePrimitive<TerminalSettings>({
  id: "terminal",
  displayName: "Terminal",
  icon: "▸_",
  category: "Shell",
  containerKind: "terminal",
  settingsFields: [
    {
      key: "workspace_slug",
      kind: "string",
      label: "Workspace",
      default: "",
      help: "The workspace whose shell this pane opens.",
    },
  ],
  parseSettings: (raw) => ({
    workspaceSlug: typeof raw.workspace_slug === "string" ? raw.workspace_slug : "",
  }),
  Component: ({ settings }) => {
    // No slug means no shell to open. Starting one against an empty workspace
    // would spawn a session the operator did not ask for and cannot reach.
    if (settings.workspaceSlug === "") {
      return <Unconfigured>This terminal has no workspace yet.</Unconfigured>;
    }
    return <TerminalPanel workspaceSlug={settings.workspaceSlug} />;
  },
});
