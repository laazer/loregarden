/**
 * Reading the docker capacity ledger.
 *
 * Own module beside `ticketEdgeApi` and `composerApi`, for the reason
 * `dockerTypes` is: `client.ts` is 734 lines and this does not need to be in it.
 */

import { request } from "./http";
import type { DockerCapacityStatus } from "./dockerTypes";

export const dockerApi = {
  /** The ceiling, its holders, and the queue behind them. */
  capacity: (): Promise<DockerCapacityStatus> => request<DockerCapacityStatus>("/api/docker/capacity"),
};
