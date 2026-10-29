import type { EditorChangeStatus } from "../../api/types";

/** The one-letter mark git users already read, and the words behind it. */
export const CHANGE_MARK: Record<EditorChangeStatus, { letter: string; label: string }> = {
  added: { letter: "A", label: "Added" },
  modified: { letter: "M", label: "Modified" },
  deleted: { letter: "D", label: "Deleted" },
  untracked: { letter: "U", label: "New, not yet added to git" },
};
