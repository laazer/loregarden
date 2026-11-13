import type { StateStorage } from "zustand/middleware";

/**
 * `storage`, minus writes of the value it already holds.
 *
 * zustand's `persist` serializes the `partialize`d slice and calls `setItem`
 * after every `set()`, including sets that only touch fields `partialize` drops.
 * For `uiStore` that was a synchronous localStorage write per search keystroke
 * and per pointermove of a dock drag, each rewriting an identical string.
 *
 * Remembers what this tab last read or wrote per key. A value another tab wrote
 * in between is not re-asserted until this tab's slice actually changes, which
 * is no worse than `persist`'s own last-writer-wins: it never synced tabs.
 */
export function skipUnchangedWrites(storage: StateStorage): StateStorage {
  const held = new Map<string, string>();
  return {
    getItem: (name) => {
      const value = storage.getItem(name);
      if (typeof value === "string") held.set(name, value);
      return value;
    },
    setItem: (name, value) => {
      if (held.get(name) === value) return;
      storage.setItem(name, value);
      held.set(name, value);
    },
    removeItem: (name) => {
      held.delete(name);
      storage.removeItem(name);
    },
  };
}
