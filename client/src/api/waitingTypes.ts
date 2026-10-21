// What a stuck ticket waits for, and who can unblock it. Split out of
// `types.ts` (at its size cap) with the waiting-on refs that grew beside them.

import type { TicketState } from "./types";

/** Who can unblock a blocked ticket — mirrors `models.domain.block_kinds`. */
export type BlockKind = "harness" | "work" | "decision" | "human_action" | "tickets" | "conflict";

/** What a stuck ticket waits for — mirrors `BlockedOn` in `models.domain.block_kinds`. */
export type BlockedOn = "person" | "tickets" | "agent";

/** A ticket another one waits on, and what it waits on in turn. */
export interface WaitingOnRef {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  reason: "blocked_child" | "prerequisite";
  blocked_on: BlockedOn | null;
  /** Its workspace, so a prerequisite elsewhere says where to find it. */
  workspace_slug?: string;
}
