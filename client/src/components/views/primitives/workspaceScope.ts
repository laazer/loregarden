/**
 * The field that says which workspace a pane's pickers draw from.
 *
 * ## Why a field rather than the sidebar
 *
 * A view is a tab full of panes, and the whole point of one is that the panes
 * can be about different things — including different workspaces. Until now
 * they could not be: every workspace-scoped list (`ticket`, `chat_session`,
 * `branch`) was fetched for whatever workspace the *sidebar* was showing, so an
 * operator on loregarden could only ever be offered loregarden's tickets. The
 * slug was storable and unreachable at the same time.
 *
 * Three primitives already had a `workspace_slug` of their own — the terminal,
 * the conversation, the two repository cards — because their *component* needs
 * one. The ticket primitives do not: `api.ticket(id)` resolves an id from any
 * workspace, so the ticket renders either way. For those, this field scopes the
 * picker and nothing else, which is what `help` says out loud. A field whose
 * only job is to narrow a list beside it is worth having when the alternative
 * is a list that cannot be narrowed at all.
 *
 * ## Empty is not broken
 *
 * The default is `""`, and the editor reads that as "fall back to the sidebar
 * workspace" — so every view stored before this field existed keeps offering
 * exactly the list it offered yesterday. Nothing needs a migration, and no pane
 * starts life showing an error because a field it never had is unset.
 */

import type { SettingsField } from "./types";

/** The wire key, spelled once — `workspaceFrom` on a sibling field must match. */
export const WORKSPACE_SCOPE_KEY = "workspace_slug";

/**
 * A `workspace_slug` choice field.
 *
 * `help` is the parameter because the reason differs by primitive: the terminal
 * opens a shell *in* the workspace, while a ticket card merely wants its picker
 * narrowed to one. Saying "the workspace" for both would describe the first
 * accurately and the second misleadingly.
 */
export function workspaceScopeField(help: string): SettingsField {
  return {
    key: WORKSPACE_SCOPE_KEY,
    kind: "choice",
    source: "workspace",
    label: "Workspace",
    default: "",
    help,
  };
}

/** The help line every picker-only scope field carries, said once. */
export const PICKER_SCOPE_HELP =
  "Which workspace's list to choose from below. Leave empty for the one in the sidebar.";
