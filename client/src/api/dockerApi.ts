/**
 * Reading the docker capacity ledger.
 *
 * Own module beside `ticketEdgeApi` and `composerApi`, for the reason
 * `dockerTypes` is: `client.ts` is 734 lines and this does not need to be in it.
 */

import { request } from "./http";
import type { DockerCapacityStatus, ForceReleaseResult } from "./dockerTypes";

export const dockerApi = {
  /** The ceiling, its holders, and the queue behind them. */
  capacity: (): Promise<DockerCapacityStatus> => request<DockerCapacityStatus>("/api/docker/capacity"),
  /**
   * End a lease: release a holder's grant, or drop a waiter from the line.
   * Driven by the `capacity.release` agent action; the MCP twin is
   * loregarden_force_release_docker_lease.
   */
  releaseLease: (leaseId: string, reason: string): Promise<ForceReleaseResult> =>
    request<ForceReleaseResult>(`/api/docker/capacity/leases/${encodeURIComponent(leaseId)}/release`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};
