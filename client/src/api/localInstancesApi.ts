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
  TemplateSpec,
  WorkspaceTemplates,
} from "./localInstancesTypes";

const BASE = "/api/instances";
const TEMPLATES = "/api/instance-templates";

export const localInstancesApi = {
  /** Every workspace's instances. */
  list: (): Promise<LocalInstanceListing> => request<LocalInstanceListing>(BASE),
  templates: (): Promise<LocalInstanceTemplate[]> => request<LocalInstanceTemplate[]>(`${BASE}/templates`),
  launch: (body: LocalInstanceLaunch): Promise<LocalInstance> =>
    request<LocalInstance>(BASE, { method: "POST", body: JSON.stringify(body) }),
  logs: (id: string): Promise<LocalInstanceLogs> =>
    request<LocalInstanceLogs>(`${BASE}/${encodeURIComponent(id)}/logs?tail=120`),
  /** Stops a running instance, or dismisses one that already exited. */
  stop: (id: string): Promise<void> => request<void>(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** Each workspace's templates, where each came from, and what is wrong with any. */
  workspaceTemplates: (): Promise<WorkspaceTemplates[]> => request<WorkspaceTemplates[]>(TEMPLATES),
  createTemplate: (slug: string, spec: TemplateSpec): Promise<WorkspaceTemplates> =>
    request<WorkspaceTemplates>(`${TEMPLATES}/${encodeURIComponent(slug)}`, {
      method: "POST",
      body: JSON.stringify(spec),
    }),
  replaceTemplate: (slug: string, spec: TemplateSpec): Promise<WorkspaceTemplates> =>
    request<WorkspaceTemplates>(`${TEMPLATES}/${encodeURIComponent(slug)}/${encodeURIComponent(spec.name)}`, {
      method: "PUT",
      body: JSON.stringify(spec),
    }),
  deleteTemplate: (slug: string, name: string): Promise<void> =>
    request<void>(`${TEMPLATES}/${encodeURIComponent(slug)}/${encodeURIComponent(name)}`, { method: "DELETE" }),
};
