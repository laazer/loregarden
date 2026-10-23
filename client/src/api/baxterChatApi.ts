import { baxterChatSessionsPath } from "../lib/chatScope";
import { request } from "./http";
import type { FeatureLoopStartRequest } from "./chatWorkbenchTypes";
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
  forkSession: (scope: string, sessionId: string, throughMessageId = "") =>
    request<BaxterChatSnapshot>(`${baxterChatSessionsPath(scope)}/${sessionId}/fork`, {
      method: "POST",
      body: JSON.stringify({ through_message_id: throughMessageId }),
    }),
};

/** The workbench's side-card writes; each returns the thread's fresh snapshot. */
export const baxterChatWorkbenchApi = {
  setFocus: (
    scope: string,
    sessionId: string,
    body: { workspace_slugs: string[]; initiative_ids: string[] },
  ) =>
    request<BaxterChatSnapshot>(`${baxterChatSessionsPath(scope)}/${sessionId}/focus`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  startFeatureLoop: (scope: string, sessionId: string, body: FeatureLoopStartRequest) =>
    request<BaxterChatSnapshot>(`${baxterChatSessionsPath(scope)}/${sessionId}/feature`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  addCriterion: (scope: string, sessionId: string, text: string) =>
    request<BaxterChatSnapshot>(`${baxterChatSessionsPath(scope)}/${sessionId}/feature/criteria`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  decideCriteria: (scope: string, sessionId: string, ids: string[], accept: boolean) =>
    request<BaxterChatSnapshot>(
      `${baxterChatSessionsPath(scope)}/${sessionId}/feature/decisions`,
      { method: "POST", body: JSON.stringify({ ids, accept }) },
    ),
};
