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

export interface LaneCount {
  lane_count: number;
  min: number;
  max: number;
}

export interface LaneResizeResult {
  lane_count: number;
  /** Lanes past the new count still finishing work; each retires when it frees. */
  retiring_lanes: number[];
  /** Entries moved out of retired lanes into the remaining ones. */
  moved_entries: number;
}

export const queueLanesApi = {
  laneCount: () => laneRequest<LaneCount>("/count"),

  /** Lowering never interrupts running work: busy lanes finish, then retire. */
  setLaneCount: (laneCount: number) =>
    laneRequest<LaneResizeResult>("/count", {
      method: "PUT",
      body: JSON.stringify({ lane_count: laneCount }),
    }),

  add: (
    slotNumber: number,
    body: {
      ticket_id: string;
      auto_approve?: boolean;
      approve_design_plans?: boolean;
      auto_repair?: boolean;
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

  /** Clear every blocked/failed card a lane is holding. Same not-a-delete as `dismiss`. */
  dismissLane: (slotNumber: number) =>
    laneRequest<{ status: string; dismissed: number }>(`/${slotNumber}/attention/dismiss`, {
      method: "POST",
    }),

  move: (entryId: string, slotNumber: number, position: number) =>
    laneRequest<{ status: string }>(`/entries/${entryId}/move`, {
      method: "POST",
      body: JSON.stringify({ slot_number: slotNumber, position }),
    }),
};
