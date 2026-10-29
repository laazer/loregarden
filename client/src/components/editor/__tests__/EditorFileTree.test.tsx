import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import type { EditorBrowseEntry, EditorBrowseResponse, EditorChangeStatus } from "../../../api/types";
import { EditorFileTree } from "../EditorFileTree";

const mockBrowse = jest.fn();
jest.mock("../../../api/client", () => ({
  api: { editorBrowse: (...args: unknown[]) => mockBrowse(...args) },
}));

const entry = (repo_path: string, kind: "directory" | "file"): EditorBrowseEntry => ({
  name: repo_path.split("/").at(-1)!,
  path: `/repo/${repo_path}`,
  repo_path,
  kind,
});
const listing = (...entries: EditorBrowseEntry[]): EditorBrowseResponse => ({
  current_path: "/repo",
  repo_path: ".",
  parent_path: null,
  parent_repo_path: null,
  context_root: ".",
  context_path: "/repo",
  entries,
});

const TREE: Record<string, EditorBrowseResponse> = {
  ".": listing(entry("client", "directory"), entry("README.md", "file")),
  client: listing(entry("client/src", "directory"), entry("client/package.json", "file")),
  "client/src": listing(entry("client/src/main.tsx", "file")),
};

function renderTree({
  selectedPath = null,
  changes = new Map<string, EditorChangeStatus>(),
  onOpenFile = jest.fn(),
}: {
  selectedPath?: string | null;
  changes?: Map<string, EditorChangeStatus>;
  onOpenFile?: jest.Mock;
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  render(
    <EditorFileTree
      workspaceSlug="loregarden"
      contextRoot="."
      selectedPath={selectedPath}
      onOpenFile={onOpenFile}
      changes={changes}
    />,
    { wrapper },
  );
  return { onOpenFile };
}

beforeEach(() => {
  mockBrowse.mockReset();
  mockBrowse.mockImplementation((_slug: string, folder: string) => Promise.resolve(TREE[folder]));
});

describe("EditorFileTree", () => {
  it("is a tree of rows, folders first, each at its level", async () => {
    renderTree();

    const tree = await screen.findByRole("tree", { name: "Repository files" });
    const client = screen.getByRole("treeitem", { name: "client" });
    expect(tree).toContainElement(client);
    expect(client).toHaveAttribute("aria-level", "1");
    expect(client).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("treeitem", { name: "README.md" })).not.toHaveAttribute("aria-expanded");
  });

  it("opens a folder in place, loading it once, and closes it again", async () => {
    renderTree();

    fireEvent.click(await screen.findByRole("treeitem", { name: "client" }));
    const nested = await screen.findByRole("treeitem", { name: "package.json" });
    expect(nested).toHaveAttribute("aria-level", "2");
    expect(screen.getByRole("treeitem", { name: "client" })).toHaveAttribute("aria-expanded", "true");
    expect(mockBrowse).toHaveBeenCalledWith("loregarden", "client", ".");

    fireEvent.click(screen.getByRole("treeitem", { name: "client" }));
    expect(screen.queryByRole("treeitem", { name: "package.json" })).not.toBeInTheDocument();
  });

  it("opens a file on click", async () => {
    const { onOpenFile } = renderTree();

    fireEvent.click(await screen.findByRole("treeitem", { name: "README.md" }));
    expect(onOpenFile).toHaveBeenCalledWith("README.md");
  });

  it("walks with the keyboard as a tree does, with one row in the tab order", async () => {
    const { onOpenFile } = renderTree();
    const client = await screen.findByRole("treeitem", { name: "client" });
    expect(client).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("treeitem", { name: "README.md" })).toHaveAttribute("tabindex", "-1");

    client.focus();
    fireEvent.keyDown(client, { key: "ArrowRight" });
    const src = await screen.findByRole("treeitem", { name: "src" });
    fireEvent.keyDown(client, { key: "ArrowRight" });
    expect(src).toHaveFocus();

    fireEvent.keyDown(src, { key: "ArrowDown" });
    expect(screen.getByRole("treeitem", { name: "package.json" })).toHaveFocus();

    // Left on a file steps out to its folder; Left on an open folder closes it.
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
    expect(screen.getByRole("treeitem", { name: "client" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
    expect(screen.queryByRole("treeitem", { name: "src" })).not.toBeInTheDocument();

    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(screen.getByRole("treeitem", { name: "README.md" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Enter" });
    expect(onOpenFile).toHaveBeenCalledWith("README.md");
  });

  it("opens the folders above a file opened from elsewhere, and marks it selected", async () => {
    renderTree({ selectedPath: "client/src/main.tsx" });

    const file = await screen.findByRole("treeitem", { name: "main.tsx" });
    expect(file).toHaveAttribute("aria-selected", "true");
    expect(file).toHaveAttribute("aria-level", "3");
    expect(file).toHaveAttribute("tabindex", "0");
  });

  it("marks changed files, and folders that hold one while closed", async () => {
    renderTree({ changes: new Map([["client/src/main.tsx", "modified"], ["README.md", "added"]]) });

    const readme = await screen.findByRole("treeitem", { name: "README.md, Added" });
    expect(readme).toHaveTextContent("A");
    expect(screen.getByRole("treeitem", { name: "client, holds changed files" })).toBeInTheDocument();
  });

  it("says why the repository would not open, and tries again on request", async () => {
    mockBrowse.mockRejectedValueOnce(new Error("Permission denied: /repo"));
    renderTree();

    expect(await screen.findByRole("alert")).toHaveTextContent("Permission denied: /repo");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("treeitem", { name: "client" })).toBeInTheDocument();
  });

  it("shows a folder that failed to open as a row that retries, and an empty one as empty", async () => {
    mockBrowse.mockImplementation((_slug: string, folder: string) => {
      if (folder === "client") return Promise.reject(new Error("Permission denied: client"));
      return Promise.resolve(folder === "." ? TREE["."] : listing());
    });
    renderTree();

    fireEvent.click(await screen.findByRole("treeitem", { name: "client" }));
    const failed = await screen.findByRole("treeitem", { name: /Permission denied: client · Try again/ });
    mockBrowse.mockImplementation((_slug: string, folder: string) =>
      Promise.resolve(folder === "." ? TREE["."] : listing()),
    );
    fireEvent.click(failed);
    expect(await screen.findByText("Empty folder")).toBeInTheDocument();
  });
});
