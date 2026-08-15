/**
 * The vocabulary a container primitive is described in.
 *
 * A primitive is "a thing a view container can show": its display metadata (so
 * a picker can offer it without knowing what it is), a settings schema (so an
 * operator can configure it without knowing what it is), and a component that
 * receives *parsed* settings.
 *
 * There is no schema library in this client, so the schema is a field
 * descriptor array plus a hand-written `parseSettings`. `parseSettings` is the
 * only narrowing step: everything downstream of it is typed.
 */

import type { ComponentType } from "react";

/** The three container kinds the server's `ContainerKind` enum names. */
export type ContainerKind = "terminal" | "panel" | "web_embed";

/** The input kinds a settings editor knows how to render. */
export type SettingsFieldKind = "string" | "number" | "boolean";

export interface SettingsField {
  /** Wire key inside the container's `settings` map — snake_case, per 433. */
  key: string;
  label: string;
  kind: SettingsFieldKind;
  /** Used whenever the stored value is missing, null, or the wrong type. */
  default: string | number | boolean;
  /** Optional one-line hint for the settings editor. */
  help?: string;
}

/** What a primitive's own component is handed: its container's id and parsed settings. */
export interface PrimitiveProps<TSettings> {
  containerId: string;
  settings: TSettings;
}

/**
 * A primitive as its author writes it — generic over the settings type
 * `parseSettings` produces and `Component` consumes.
 */
export interface PrimitiveEntry<TSettings> {
  /** Stable registry key; also the value stored as `settings.primitive_id`. */
  id: string;
  displayName: string;
  icon: string;
  category: string;
  containerKind: ContainerKind;
  settingsFields: SettingsField[];
  parseSettings: (raw: Record<string, unknown>) => TSettings;
  Component: ComponentType<PrimitiveProps<TSettings>>;
}

/**
 * A primitive as the registry holds it, with `TSettings` erased.
 *
 * The erasure happens inside `definePrimitive`, which closes over the generic
 * and wraps the component so it is handed `parseSettings(raw)`. That is why no
 * entry in this registry needs a cast: the only place that knows `TSettings` is
 * the one place that is still generic.
 */
export interface RegisteredPrimitive {
  id: string;
  displayName: string;
  icon: string;
  category: string;
  containerKind: ContainerKind;
  settingsFields: SettingsField[];
  parseSettings: (raw: Record<string, unknown>) => unknown;
  Component: ComponentType<PrimitiveProps<Record<string, unknown>>>;
}
