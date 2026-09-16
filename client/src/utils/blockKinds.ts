import type { BlockKind } from "../api/types";

/** Who can unblock it, as a badge word. */
export function blockKindLabel(kind: BlockKind): string {
  switch (kind) {
    case "harness":
      return "harness";
    case "work":
      return "agent work";
    case "decision":
      return "your decision";
    case "human_action":
      return "your action";
  }
}

/** What the kind means for the person reading it — whether they need to act. */
export function blockKindMeaning(kind: BlockKind): string {
  switch (kind) {
    case "harness":
      return "The environment or the control plane failed, not the work. No action needed from you; the control plane retries or repairs it.";
    case "work":
      return "The agent stopped on something an agent can still do. No action needed from you; a repair run picks it up.";
    case "decision":
      return "A choice only you should make. Answer the question in the inbox and the stage reruns on its own.";
    case "human_action":
      return "Needs your hands — credentials, hardware, or an external account. The inbox item says what.";
  }
}
