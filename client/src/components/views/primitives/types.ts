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

interface SettingsFieldBase {
  /** Wire key inside the container's `settings` map — snake_case, per 433. */
  key: string;
  label: string;
  /** Optional one-line hint for the settings editor. */
  help?: string;
}

/**
 * A settings field, discriminated on `kind`.
 *
 * `kind` and `default` are one decision, not two: `{kind: "number", default:
 * "twelve"}` is a schema that no editor can render and no `parseSettings` can
 * honour, and a shared `string | number | boolean` default type accepts it. The
 * union also means a settings editor (438) can switch on `kind` and get the
 * matching `default` type without narrowing it by hand.
 */
export type SettingsField =
  | (SettingsFieldBase & { kind: "string"; default: string })
  | (SettingsFieldBase & { kind: "number"; default: number })
  | (SettingsFieldBase & { kind: "boolean"; default: boolean });

/**
 * A primitive's parsed settings: an open, JSON-shaped map.
 *
 * The constraint exists so the registry's erased `parseSettings` can promise a
 * usable return type rather than `unknown`. Write a primitive's settings as a
 * `type` alias, not an `interface` — TypeScript gives object type aliases an
 * implicit index signature and interfaces none, so an interface will not
 * satisfy this bound.
 */
export type ParsedSettings = Record<string, unknown>;

/** What a primitive's own component is handed: its container's id and parsed settings. */
export interface PrimitiveProps<TSettings> {
  containerId: string;
  settings: TSettings;
}

/**
 * A container as 433's wire model holds it: no id (it is the key of the
 * container registry), a `kind` from the server enum, and an open settings map
 * carrying `primitive_id`.
 */
export interface ViewContainer {
  kind: ContainerKind;
  settings: Record<string, unknown>;
}

/**
 * A primitive as its author writes it — generic over the settings type
 * `parseSettings` produces and `Component` consumes.
 */
export interface PrimitiveEntry<TSettings extends ParsedSettings> {
  /** Stable registry key; also the value stored as `settings.primitive_id`. */
  id: string;
  displayName: string;
  icon: string;
  category: string;
  /**
   * The `kind` a container holding this primitive must be stored as.
   *
   * Checked, not advisory: `newContainerFor` stamps it, and
   * `ContainerPrimitiveHost` refuses to mount a primitive whose entry disagrees
   * with the kind the container was stored under.
   */
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
  /**
   * Erased down to the bound, not to `unknown`: a consumer that wants to read a
   * parsed value back (a settings editor previewing a default, a test) gets a
   * usable map without an assertion, and the primitive's own component still
   * sees the precise type because `definePrimitive` applies this before the
   * erasure.
   */
  parseSettings: (raw: Record<string, unknown>) => ParsedSettings;
  Component: ComponentType<PrimitiveProps<Record<string, unknown>>>;
}
