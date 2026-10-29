/**
 * The Editor's file tree, flattened into the rows a person scans.
 *
 * Folders load lazily, one listing per expanded folder, so the tree is a map of
 * listings keyed by folder plus the set of folders that are open. Flattening it
 * into one ordered list is what lets the arrow keys walk it as a single column,
 * the way the WAI tree pattern expects.
 */

import type { EditorBrowseEntry, EditorChangeStatus } from "../api/types";

/** The root folder's key; the server lists it for `path="."`. */
export const TREE_ROOT = ".";

/** One folder's listing as the tree sees it while it loads, fails or arrives. */
export type FolderListing =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; entries: EditorBrowseEntry[] };

export type TreeRow =
  | { kind: "directory"; path: string; name: string; depth: number; expanded: boolean }
  | { kind: "file"; path: string; name: string; depth: number }
  /** A folder's children are loading, failed to load, or there are none. */
  | { kind: "loading" | "empty"; path: string; depth: number }
  | { kind: "error"; path: string; depth: number; message: string };

/**
 * Walk the open folders depth-first into rows. A folder that is open but not
 * listed yet gets one placeholder row under it, so the tree says what it is
 * waiting for instead of looking empty.
 */
export function flattenTree(
  listings: ReadonlyMap<string, FolderListing>,
  expanded: ReadonlySet<string>,
): TreeRow[] {
  const rows: TreeRow[] = [];
  const walk = (folder: string, depth: number) => {
    const listing = listings.get(folder) ?? { state: "loading" };
    if (listing.state === "loading") {
      rows.push({ kind: "loading", path: folder, depth });
      return;
    }
    if (listing.state === "error") {
      rows.push({ kind: "error", path: folder, depth, message: listing.message });
      return;
    }
    if (listing.entries.length === 0) {
      rows.push({ kind: "empty", path: folder, depth });
      return;
    }
    for (const entry of listing.entries) {
      if (entry.kind === "directory") {
        const open = expanded.has(entry.repo_path);
        rows.push({ kind: "directory", path: entry.repo_path, name: entry.name, depth, expanded: open });
        if (open) walk(entry.repo_path, depth + 1);
      } else {
        rows.push({ kind: "file", path: entry.repo_path, name: entry.name, depth });
      }
    }
  };
  walk(TREE_ROOT, 0);
  return rows;
}

/** `a/b/c.ts` → `["a", "a/b"]`: the folders to open so the file shows. */
export function ancestorFolders(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

/** The folder a row sits in, for Left on a row that has nothing to collapse. */
export function parentFolder(path: string): string | null {
  const at = path.lastIndexOf("/");
  return at < 0 ? null : path.slice(0, at);
}

/** Every folder holding a changed file, so a collapsed folder can still say so. */
export function foldersWithChanges(changes: ReadonlyMap<string, EditorChangeStatus>): Set<string> {
  const folders = new Set<string>();
  for (const path of changes.keys()) {
    for (const folder of ancestorFolders(path)) folders.add(folder);
  }
  return folders;
}

/** Rows the arrow keys stop on. Loading and empty placeholders are read, not visited. */
export function isNavigable(row: TreeRow): boolean {
  return row.kind === "directory" || row.kind === "file" || row.kind === "error";
}

/** A stable key per row: a placeholder shares its folder's path. */
export function rowKey(row: TreeRow): string {
  return row.kind === "directory" || row.kind === "file" ? row.path : `${row.kind}:${row.path}`;
}
