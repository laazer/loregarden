import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { renderWithRouter } from "../../test/renderWithRouter";
import { useUiStore } from "../../state/uiStore";
import { EditorPage } from "../EditorPage";

jest.mock("../../components/TopbarPageSlot", () => ({
  PageTopbar: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const mockApi = {
  workspaces: jest.fn(),
  editorRefs: jest.fn(),
  editorBrowse: jest.fn(),
  editorChanges: jest.fn(),
  editorReadFile: jest.fn(),
  editorWriteFile: jest.fn(),
  editorCheckout: jest.fn(),
};
jest.mock("../../api/client", () => ({ api: new Proxy({}, { get: (_t, key: string) => mockApi[key as keyof typeof mockApi] }) }));

const entry = (repo_path: string) => ({ name: repo_path, path: `/repo/${repo_path}`, repo_path, kind: "file" as const });

beforeEach(() => {
  useUiStore.setState({ editorWorkspace: "loregarden", editorContextRoot: ".", editorFilePath: null, editorRecentFiles: {} });
  mockApi.workspaces.mockResolvedValue([{ slug: "loregarden", name: "loregarden", repo_exists: true }]);
  mockApi.editorRefs.mockResolvedValue({
    workspace_root: "/repo",
    context_root: ".",
    context_path: "/repo",
    current_branch: "main",
    branches: [],
    worktrees: [],
  });
  mockApi.editorBrowse.mockResolvedValue({
    current_path: "/repo",
    repo_path: ".",
    parent_path: null,
    parent_repo_path: null,
    context_root: ".",
    context_path: "/repo",
    entries: [entry("a.ts"), entry("b.ts")],
  });
  mockApi.editorChanges.mockResolvedValue({ branch: "ticket/x", base: "main", merge_base: "abc", files: [{ path: "a.ts", status: "modified", additions: 1, deletions: 0 }], total: 1 });
  mockApi.editorReadFile.mockImplementation((_slug: string, path: string) =>
    Promise.resolve({ path, content: `// ${path}\n`, language: "typescript", size: 10 }),
  );
});

describe("EditorPage", () => {
  it("starts on what the branch changed, opens a file from it, and remembers it", async () => {
    renderWithRouter(<EditorPage />);

    const changed = await screen.findByRole("region", { name: /Changed on ticket\/x/ });
    fireEvent.click(await within(changed).findByRole("button", { name: /a\.ts/ }));
    expect(await screen.findByTestId("monaco-editor")).toHaveValue("// a.ts\n");
    expect(useUiStore.getState().editorRecentFiles["loregarden::."]).toEqual(["a.ts"]);

    fireEvent.click(screen.getByRole("button", { name: "Close a.ts" }));
    expect(await screen.findByRole("region", { name: "Recent" })).toHaveTextContent("a.ts");
  });

  it("asks before another file discards unsaved edits, and stays put when told no", async () => {
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(false);
    useUiStore.setState({ editorFilePath: "a.ts" });
    renderWithRouter(<EditorPage />);

    await waitFor(() => expect(screen.getByTestId("monaco-editor")).toHaveValue("// a.ts\n"));
    fireEvent.change(screen.getByTestId("monaco-editor"), { target: { value: "edited" } });
    fireEvent.click(await screen.findByRole("treeitem", { name: /b\.ts/ }));

    expect(confirm).toHaveBeenCalledWith("Discard unsaved changes to a.ts?");
    expect(useUiStore.getState().editorFilePath).toBe("a.ts");
    expect(screen.getByTestId("monaco-editor")).toHaveValue("edited");

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("treeitem", { name: /b\.ts/ }));
    expect(useUiStore.getState().editorFilePath).toBe("b.ts");
    confirm.mockRestore();
  });
});
