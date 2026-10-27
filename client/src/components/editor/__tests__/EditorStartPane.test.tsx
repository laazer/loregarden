import { fireEvent, render, screen, within } from "@testing-library/react";

import type { EditorChange, EditorChangesResponse } from "../../../api/types";
import { EditorStartPane } from "../EditorStartPane";

const change = (path: string, status: EditorChange["status"] = "modified", additions: number | null = 3, deletions: number | null = 1): EditorChange => ({
  path,
  status,
  additions,
  deletions,
});

const changes = (files: EditorChange[], total = files.length): EditorChangesResponse => ({
  branch: "ticket/lg-x-1",
  base: "main",
  merge_base: "abc123def456",
  files,
  total,
});

function renderPane(props: Partial<Parameters<typeof EditorStartPane>[0]> = {}) {
  const onOpenFile = jest.fn();
  render(
    <EditorStartPane
      changes={undefined}
      changesLoading={false}
      changesError={null}
      recent={[]}
      onOpenFile={onOpenFile}
      {...props}
    />,
  );
  return { onOpenFile };
}

describe("EditorStartPane", () => {
  it("lists what the branch changed, each row opening its file", () => {
    const { onOpenFile } = renderPane({
      changes: changes([change("client/src/main.tsx"), change("server/app.py", "added", 40, 0)]),
    });

    const section = screen.getByRole("region", { name: /Changed on ticket\/lg-x-1/ });
    expect(within(section).getByText(/Since it left/)).toHaveTextContent("main");
    const row = within(section).getByRole("button", { name: /main\.tsx/ });
    expect(row).toHaveTextContent("client/src");
    expect(row).toHaveTextContent("+3 −1");
    fireEvent.click(row);
    expect(onOpenFile).toHaveBeenCalledWith("client/src/main.tsx");
  });

  it("keeps a deleted file in the list without offering to open it", () => {
    renderPane({ changes: changes([change("old.ts", "deleted", 0, 12)]) });

    expect(screen.getByText("old.ts")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /old\.ts/ })).not.toBeInTheDocument();
  });

  it("leaves out counts git has none for", () => {
    renderPane({ changes: changes([change("logo.png", "added", null, null)]) });

    expect(screen.getByRole("button", { name: /logo\.png/ })).not.toHaveTextContent("+");
  });

  it("says when nothing changed, apart from when it could not tell", () => {
    renderPane({ changes: changes([]) });
    expect(screen.getByText(/Nothing changed on ticket\/lg-x-1 since it left main/)).toBeInTheDocument();
  });

  it("names the failure instead of claiming nothing changed", () => {
    renderPane({ changesError: new Error("No base branch to compare with") });

    expect(screen.getByText("No base branch to compare with")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing changed/)).not.toBeInTheDocument();
  });

  it("adds a filter past fifty files and counts what the cap left out", () => {
    const files = Array.from({ length: 60 }, (_, index) => change(`src/file${index}.ts`));
    renderPane({ changes: changes(files, 260) });

    fireEvent.change(screen.getByRole("textbox", { name: "Filter changed files" }), { target: { value: "file5" } });
    // file5 and file50–59.
    expect(screen.getAllByRole("button", { name: /file5/ })).toHaveLength(11);
    expect(screen.getByText(/And 200 more/)).toBeInTheDocument();
  });

  it("lists recent files newest first, and says where they come from when there are none", () => {
    const { onOpenFile } = renderPane({ recent: ["b/two.ts", "one.ts"] });

    const recent = screen.getByRole("region", { name: "Recent" });
    const rows = within(recent).getAllByRole("button");
    expect(rows.map((row) => row.textContent)).toEqual(["two.tsb", "one.ts"]);
    fireEvent.click(rows[0]);
    expect(onOpenFile).toHaveBeenCalledWith("b/two.ts");
  });

  it("explains an empty recent list", () => {
    renderPane();
    expect(screen.getByText(/Files you open here appear in this list/)).toBeInTheDocument();
  });
});
