import { request } from "./http";
import type { BaxterChatSnapshot } from "./types";

/**
 * Baxter Home chat endpoints that don't belong on the already-oversized
 * `api` object in `client.ts` (organization gate: 500-line max).
 */
export const baxterChatApi = {
  /**
   * Copy settled messages into a new session; the source thread is untouched.
   *
   * `throughMessageId` branches from a point in the thread rather than its end
   * — the per-turn Fork action. Omitted, the whole conversation copies, which
   * is what `/fork` and the history rail ask for.
   */
  forkSession: (slug: string, sessionId: string, throughMessageId = "") =>
    request<BaxterChatSnapshot>(
      `/api/workspaces/${encodeURIComponent(slug)}/baxter-chat/sessions/${sessionId}/fork`,
      { method: "POST", body: JSON.stringify({ through_message_id: throughMessageId }) },
    ),
};
