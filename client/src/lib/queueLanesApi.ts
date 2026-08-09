/**
 * Mutations on the execution lanes.
 *
 * There is no "start": adding a ticket to an idle lane starts it, and adding to
 * a busy one queues it behind whatever is there. That is the model, not a
 * shortcut — a queued entry has to start itself when the lane drains.
 */

import { API_BASE } from "../api/client";

async function laneRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/api/parallel/lanes${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export interface AddToLaneResult {
  status: "started" | "queued";
  slot_number: number;
  entry_id: string;
  position?: number;
  message: string;
}

export const queueLanesApi = {
  add: (
    slotNumber: number,
    body: {
      ticket_id: string;
      auto_approve?: boolean;
      stop_at_stage_key?: string;
      timeout_seconds?: number;
    },
  ) =>
    laneRequest<AddToLaneResult>(`/${slotNumber}/entries`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  remove: (entryId: string) =>
    laneRequest<{ status: string }>(`/entries/${entryId}`, { method: "DELETE" }),

  /**
   * Clear a blocked/failed entry from its lane's needs-attention section.
   *
   * Not a delete: the entry stays in queue history. This only records that
   * someone has seen it, which is why it has to reach the server — a section
   * that emptied itself on reload would be no better than not having one.
   */
  dismiss: (entryId: string) =>
    laneRequest<{ status: string }>(`/entries/${entryId}/dismiss`, { method: "POST" }),

  move: (entryId: string, slotNumber: number, position: number) =>
    laneRequest<{ status: string }>(`/entries/${entryId}/move`, {
      method: "POST",
      body: JSON.stringify({ slot_number: slotNumber, position }),
    }),
};
