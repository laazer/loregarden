/**
 * Local instances: branch servers and clients this control plane launched,
 * and the main server that advertised itself. Own module, like `dockerApi`,
 * because `client.ts` does not need to grow for it.
 */

import { request } from "./http";
import type {
  LocalInstance,
  LocalInstanceLaunch,
  LocalInstanceListing,
  LocalInstanceLogs,
  LocalInstanceTemplate,
} from "./localInstancesTypes";

const BASE = "/api/instances";

export const localInstancesApi = {
  list: (): Promise<LocalInstanceListing> => request<LocalInstanceListing>(`${BASE}?project=loregarden`),
  templates: (): Promise<LocalInstanceTemplate[]> => request<LocalInstanceTemplate[]>(`${BASE}/templates`),
  launch: (body: LocalInstanceLaunch): Promise<LocalInstance> =>
    request<LocalInstance>(BASE, { method: "POST", body: JSON.stringify(body) }),
  logs: (id: string): Promise<LocalInstanceLogs> =>
    request<LocalInstanceLogs>(`${BASE}/${encodeURIComponent(id)}/logs?tail=120`),
  /** Stops a running instance, or dismisses one that already exited. */
  stop: (id: string): Promise<void> => request<void>(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
