import { request } from "./http";
import type { BaxterChatSnapshot } from "./types";

/**
 * Baxter Home chat endpoints that don't belong on the already-oversized
 * `api` object in `client.ts` (organization gate: 500-line max).
 */
export const baxterChatApi = {
  /** Copy settled messages into a new session; the source thread is untouched. */
  forkSession: (slug: string, sessionId: string) =>
    request<BaxterChatSnapshot>(
      `/api/workspaces/${encodeURIComponent(slug)}/baxter-chat/sessions/${sessionId}/fork`,
      { method: "POST" },
    ),
};
