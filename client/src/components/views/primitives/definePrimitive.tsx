/**
 * The one place a primitive's settings generic is erased.
 *
 * `definePrimitive` is generic over the *parsed* settings type. It returns a
 * `RegisteredPrimitive` whose component takes the raw settings map and runs
 * `parseSettings` itself, so the generic never has to appear in the registry's
 * value type — and no entry has to be asserted into it.
 */

import type {
  ParsedSettings,
  PrimitiveEntry,
  PrimitiveProps,
  RegisteredPrimitive,
} from "./types";

export function definePrimitive<TSettings extends ParsedSettings>(
  entry: PrimitiveEntry<TSettings>,
): RegisteredPrimitive {
  const Inner = entry.Component;

  function PrimitiveHost({ containerId, settings }: PrimitiveProps<Record<string, unknown>>) {
    return <Inner containerId={containerId} settings={entry.parseSettings(settings)} />;
  }
  PrimitiveHost.displayName = `Primitive(${entry.id})`;

  return {
    id: entry.id,
    displayName: entry.displayName,
    icon: entry.icon,
    category: entry.category,
    containerKind: entry.containerKind,
    settingsFields: entry.settingsFields,
    parseSettings: entry.parseSettings,
    Component: PrimitiveHost,
  };
}
