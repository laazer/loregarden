import { useQueries } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { api } from "../../api/client";
import type { EditorChangeStatus } from "../../api/types";
import {
  TREE_ROOT,
  ancestorFolders,
  flattenTree,
  foldersWithChanges,
  isNavigable,
  parentFolder,
  rowKey,
  type FolderListing,
  type TreeRow,
} from "../../lib/editorTree";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { CHANGE_MARK } from "./editorChangeMarks";

import "./EditorFileTree.css";

interface EditorFileTreeProps {
  workspaceSlug: string;
  contextRoot: string;
  selectedPath: string | null;
  onOpenFile: (path: string) => void;
  /** Files the open branch changed, marked in the tree; folders holding one get a dot. */
  changes: ReadonlyMap<string, EditorChangeStatus>;
}

/**
 * The repository as a tree: 24px rows with indent guides, folders opening in
 * place.
 *
 * A file tree is scanned, so it is dense and flat: no card per row, no emoji,
 * the name in the data face. Folders load when first opened and stay cached.
 * The keyboard follows the WAI tree pattern (Up/Down move, Right opens or
 * steps in, Left closes or steps out, Home/End, Enter opens), with one row in
 * the tab order at a time.
 *
 * Mount it with a `key` per workspace and worktree: what is open in one says
 * nothing about another.
 */
export function EditorFileTree({
  workspaceSlug,
  contextRoot,
  selectedPath,
  onOpenFile,
  changes,
}: EditorFileTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());

  // A file opened from elsewhere (the changed list, recent files, a link) is
  // shown where it lives, so the tree and the editor agree.
  useEffect(() => {
    if (!selectedPath) return;
    const folders = ancestorFolders(selectedPath);
    if (folders.length === 0) return;
    setExpanded((was) => (folders.every((folder) => was.has(folder)) ? was : new Set([...was, ...folders])));
  }, [selectedPath]);

  const folders = useMemo(() => [TREE_ROOT, ...expanded], [expanded]);
  const results = useQueries({
    queries: folders.map((folder) => ({
      queryKey: ["editor-browse", workspaceSlug, contextRoot, folder],
      queryFn: () => api.editorBrowse(workspaceSlug, folder, contextRoot),
      enabled: Boolean(workspaceSlug),
    })),
  });

  const listings = new Map<string, FolderListing>();
  const refetchers = new Map<string, () => void>();
  folders.forEach((folder, index) => {
    const result = results[index];
    refetchers.set(folder, () => void result.refetch());
    if (result.data) listings.set(folder, { state: "ready", entries: result.data.entries });
    else if (result.error) {
      listings.set(folder, { state: "error", message: describeError(result.error, "Couldn't open this folder") });
    } else listings.set(folder, { state: "loading" });
  });

  const rows = flattenTree(listings, expanded);
  const selectedShown = rows.some((row) => row.kind === "file" && row.path === selectedPath);

  // Once the opened file's folders have loaded, bring its row into view: in a
  // long tree it can land well below the fold.
  useEffect(() => {
    if (selectedPath && selectedShown) rowRefs.current.get(selectedPath)?.scrollIntoView?.({ block: "nearest" });
  }, [selectedPath, selectedShown]);
  const navigable = rows.filter(isNavigable);
  const changedFolders = useMemo(() => foldersWithChanges(changes), [changes]);
  const root = listings.get(TREE_ROOT);

  const tabStop =
    navigable.find((row) => rowKey(row) === focusedKey) ??
    navigable.find((row) => row.kind === "file" && row.path === selectedPath) ??
    navigable[0];

  const toggle = (path: string, open: boolean) =>
    setExpanded((was) => {
      const next = new Set(was);
      if (open) next.add(path);
      else for (const folder of was) if (folder === path || folder.startsWith(`${path}/`)) next.delete(folder);
      return next;
    });

  const moveTo = (row: TreeRow | undefined) => {
    if (!row) return;
    const key = rowKey(row);
    setFocusedKey(key);
    rowRefs.current.get(key)?.focus();
  };

  const activate = (row: TreeRow) => {
    if (row.kind === "directory") toggle(row.path, !row.expanded);
    else if (row.kind === "file") onOpenFile(row.path);
    else if (row.kind === "error") refetchers.get(row.path)?.();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = tabStop;
    if (!current) return;
    const at = navigable.indexOf(current);
    let handled = true;
    switch (event.key) {
      case "ArrowDown":
        moveTo(navigable[at + 1]);
        break;
      case "ArrowUp":
        moveTo(navigable[at - 1]);
        break;
      case "Home":
        moveTo(navigable[0]);
        break;
      case "End":
        moveTo(navigable.at(-1));
        break;
      case "ArrowRight":
        if (current.kind === "directory" && !current.expanded) toggle(current.path, true);
        else if (current.kind === "directory") moveTo(navigable[at + 1]);
        break;
      case "ArrowLeft": {
        if (current.kind === "directory" && current.expanded) {
          toggle(current.path, false);
          break;
        }
        const parent = parentFolder(current.path);
        moveTo(navigable.find((row) => row.kind === "directory" && row.path === parent));
        break;
      }
      case "Enter":
      case " ":
        activate(current);
        break;
      default:
        handled = false;
    }
    if (handled) event.preventDefault();
  };

  if (!root || root.state === "loading") {
    return <PaneSkeleton variant="list" label="Opening the repository…" />;
  }
  if (root.state === "error") {
    return (
      <div className="editor-tree-failed" role="alert">
        <p>{root.message}</p>
        <Button variant="secondary" compact onClick={() => refetchers.get(TREE_ROOT)?.()}>
          Try again
        </Button>
      </div>
    );
  }

  return (
    <div className="editor-tree" role="tree" aria-label="Repository files" onKeyDown={onKeyDown}>
      {rows.map((row) => {
        const key = rowKey(row);
        const guides = Array.from({ length: row.depth }, (_, level) => (
          <span key={level} className="editor-tree-guide" aria-hidden />
        ));
        if (row.kind === "loading" || row.kind === "empty") {
          return (
            <div key={key} className="editor-tree-row editor-tree-note" role="none">
              {guides}
              <span className="editor-tree-twisty" aria-hidden />
              <span>{row.kind === "loading" ? "Loading…" : "Empty folder"}</span>
            </div>
          );
        }
        const isTabStop = tabStop !== undefined && rowKey(tabStop) === key;
        const common = {
          ref: (node: HTMLDivElement | null) => {
            if (node) rowRefs.current.set(key, node);
            else rowRefs.current.delete(key);
          },
          tabIndex: isTabStop ? 0 : -1,
          onFocus: () => setFocusedKey(key),
          onClick: () => {
            setFocusedKey(key);
            activate(row);
          },
          "aria-level": row.depth + 1,
        };
        if (row.kind === "error") {
          return (
            <div key={key} {...common} className="editor-tree-row editor-tree-error" role="treeitem" title="Enter to try again">
              {guides}
              <span className="editor-tree-twisty" aria-hidden />
              <span className="editor-tree-name">{row.message} · Try again</span>
            </div>
          );
        }
        if (row.kind !== "directory" && row.kind !== "file") return null;
        const mark = row.kind === "file" ? changes.get(row.path) : undefined;
        const holdsChanges = row.kind === "directory" && changedFolders.has(row.path);
        // The mark is a letter on screen; a screen reader gets the word.
        const spoken = mark ? `${row.name}, ${CHANGE_MARK[mark].label}` : holdsChanges ? `${row.name}, holds changed files` : undefined;
        return (
          <div
            key={key}
            {...common}
            className={`editor-tree-row${row.kind === "file" && row.path === selectedPath ? " is-selected" : ""}`}
            role="treeitem"
            aria-expanded={row.kind === "directory" ? row.expanded : undefined}
            aria-selected={row.kind === "file" ? row.path === selectedPath : undefined}
            aria-label={spoken}
            title={row.path}
          >
            {guides}
            <span className="editor-tree-twisty" aria-hidden>
              {row.kind === "directory" ? (
                <svg width="10" height="10" viewBox="0 0 10 10" className={row.expanded ? "is-open" : undefined}>
                  <path d="M3.5 2 L7 5 L3.5 8" fill="none" stroke="currentColor" strokeWidth="1.4" />
                </svg>
              ) : null}
            </span>
            <span className={`editor-tree-name${row.kind === "directory" ? " is-folder" : ""}`}>{row.name}</span>
            {mark ? (
              <span className={`editor-tree-mark is-${mark}`} title={CHANGE_MARK[mark].label}>
                {CHANGE_MARK[mark].letter}
              </span>
            ) : holdsChanges ? (
              <span className="editor-tree-dot" title="Holds changed files" />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
