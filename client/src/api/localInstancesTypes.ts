/**
 * The instances router's wire shapes — lore-eden's `lore_eden.instances`,
 * mounted at `/api/instances` by `server/loregarden/api/local_instances.py`.
 *
 * `@lore-eden/ui` publishes the same types with a ready-made panel. They are
 * restated here because nothing in this client resolves that package yet;
 * the panel below follows this app's own modal and toast conventions instead.
 */

export type LocalInstanceKind = "server" | "client";
export type LocalInstanceState = "starting" | "ready" | "stalled" | "exited";

export interface LocalInstance {
  id: string;
  project: string;
  name: string;
  kind: LocalInstanceKind;
  role: "main" | "branch";
  template: string | null;
  managed: boolean;
  url: string;
  health_path: string | null;
  ready_timeout_seconds: number;
  log_path: string | null;
  target_instance_id: string | null;
  labels: Record<string, string>;
  started_at: string;
  exit_code: number | null;
  last_error: string | null;
  state: LocalInstanceState;
}

export interface LocalInstanceListing {
  instances: LocalInstance[];
  unreadable: { path: string; error: string }[];
}

export interface LocalInstanceTemplateParam {
  key: string;
  label: string;
  description: string;
  required: boolean;
  default: string | null;
  choices: string[] | null;
}

export interface LocalInstanceTemplate {
  name: string;
  kind: LocalInstanceKind;
  description: string;
  params: LocalInstanceTemplateParam[];
}

export interface LocalInstanceLaunch {
  template: string;
  name?: string;
  params: Record<string, string>;
}

export interface LocalInstanceLogs {
  path: string | null;
  lines: string[];
  truncated: boolean;
}
