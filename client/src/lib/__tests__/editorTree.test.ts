import type { EditorBrowseEntry } from "../../api/types";
import {
  TREE_ROOT,
  ancestorFolders,
  flattenTree,
  foldersWithChanges,
  isNavigable,
  parentFolder,
  type FolderListing,
} from "../editorTree";

const dir = (repo_path: string): EditorBrowseEntry => ({
  name: repo_path.split("/").at(-1)!,
  path: `/repo/${repo_path}`,
  repo_path,
  kind: "directory",
});
const file = (repo_path: string): EditorBrowseEntry => ({ ...dir(repo_path), kind: "file" });
const ready = (...entries: EditorBrowseEntry[]): FolderListing => ({ state: "ready", entries });

describe("flattenTree", () => {
  it("walks open folders depth-first, one level deeper per folder", () => {
    const listings = new Map<string, FolderListing>([
      [TREE_ROOT, ready(dir("client"), dir("server"), file("README.md"))],
      ["client", ready(dir("client/src"), file("client/package.json"))],
      ["client/src", ready(file("client/src/main.tsx"))],
    ]);

    const rows = flattenTree(listings, new Set(["client", "client/src"]));

    expect(rows.map((row) => [row.kind, row.path, row.depth])).toEqual([
      ["directory", "client", 0],
      ["directory", "client/src", 1],
      ["file", "client/src/main.tsx", 2],
      ["file", "client/package.json", 1],
      ["directory", "server", 0],
      ["file", "README.md", 0],
    ]);
    expect(rows[0]).toMatchObject({ expanded: true });
    expect(rows[4]).toMatchObject({ expanded: false });
  });

  it("puts one placeholder under an open folder that is loading, failed or empty", () => {
    const listings = new Map<string, FolderListing>([
      [TREE_ROOT, ready(dir("a"), dir("b"), dir("c"))],
      ["b", { state: "error", message: "Permission denied" }],
      ["c", ready()],
    ]);

    const rows = flattenTree(listings, new Set(["a", "b", "c"]));

    expect(rows.map((row) => [row.kind, row.path, row.depth])).toEqual([
      ["directory", "a", 0],
      ["loading", "a", 1],
      ["directory", "b", 0],
      ["error", "b", 1],
      ["directory", "c", 0],
      ["empty", "c", 1],
    ]);
    expect(rows[3]).toMatchObject({ message: "Permission denied" });
  });

  it("lets the arrow keys stop on entries and errors, never on placeholders", () => {
    const listings = new Map<string, FolderListing>([[TREE_ROOT, ready(dir("a"), dir("b"))], ["b", ready()]]);
    const rows = flattenTree(listings, new Set(["a", "b"]));

    expect(rows.filter(isNavigable).map((row) => row.kind)).toEqual(["directory", "directory"]);
  });
});

describe("paths", () => {
  it("names the folders to open so a file shows, and the folder a row sits in", () => {
    expect(ancestorFolders("client/src/main.tsx")).toEqual(["client", "client/src"]);
    expect(ancestorFolders("README.md")).toEqual([]);
    expect(parentFolder("client/src/main.tsx")).toBe("client/src");
    expect(parentFolder("README.md")).toBeNull();
  });

  it("marks every folder above a changed file", () => {
    const folders = foldersWithChanges(new Map([["client/src/main.tsx", "modified" as const]]));
    expect([...folders].sort()).toEqual(["client", "client/src"]);
  });
});
