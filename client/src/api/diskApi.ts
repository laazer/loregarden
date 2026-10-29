/**
 * Machine → Disk: what is using the disk, and the two-step cleanup.
 *
 * Its own module, like `dockerApi`, rather than more of `client.ts`. Cleanup is
 * a person's action by design: no agent action drives `executeCleanup`, and the
 * MCP tool over the same scan (loregarden_disk_usage) only reads sizes.
 */

import { request } from "./http";
import type {
  DiskCleanupEvent,
  DiskCleanupPlan,
  DiskCleanupResult,
  DiskNode,
  DiskOverview,
  DiskScanStatus,
} from "./diskTypes";

const BASE = "/api/machine/disk";

export const diskApi = {
  overview: (): Promise<DiskOverview> => request<DiskOverview>(BASE),
  scan: (): Promise<DiskScanStatus> =>
    request<DiskScanStatus>(`${BASE}/scan`, { method: "POST" }),
  /** One node (or every root, for an empty path) cut to `depth` levels. */
  tree: (path: string, depth = 2): Promise<DiskNode[]> =>
    request<DiskNode[]>(
      `${BASE}/tree?${new URLSearchParams({ path, depth: String(depth) })}`,
    ),
  addRoot: (path: string): Promise<string[]> =>
    request<string[]>(`${BASE}/roots`, {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  removeRoot: (path: string): Promise<string[]> =>
    request<string[]>(`${BASE}/roots?${new URLSearchParams({ path })}`, {
      method: "DELETE",
    }),
  previewCleanup: (paths: string[]): Promise<DiskCleanupPlan> =>
    request<DiskCleanupPlan>(`${BASE}/cleanup/preview`, {
      method: "POST",
      body: JSON.stringify({ paths }),
    }),
  executeCleanup: (
    planId: string,
    paths: string[],
  ): Promise<DiskCleanupResult> =>
    request<DiskCleanupResult>(`${BASE}/cleanup/execute`, {
      method: "POST",
      body: JSON.stringify({ plan_id: planId, paths }),
    }),
  history: (limit = 10): Promise<DiskCleanupEvent[]> =>
    request<DiskCleanupEvent[]>(`${BASE}/cleanup/history?limit=${limit}`),
};
