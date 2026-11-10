/**
 * The scanned-folders dialog's automatic list, and its keyboard path.
 *
 * lg-milestone-that-2215: `.disk-folders-list` is a bounded `overflow: auto`
 * <ul>, and it has two element sites that disagree. The "Added folders" site
 * holds a `Remove` button per row and is already reachable, so it gets no
 * change; the "Scanned automatically" site is read-only paths and held no tab
 * stop at all — which matters because this is the longer of the two, one row
 * per workspace plus one per worktree.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { DiskFoldersModal } from "../DiskFoldersModal";
import type { DiskRootSummary } from "../../../api/diskTypes";

function root(path: string, origin: DiskRootSummary["origin"]): DiskRootSummary {
  return { path, label: path, origin, workspace_slug: "loregarden" };
}

const AUTOMATIC = [
  root("/Users/jacobbrandt/workspace/loregarden", "workspace"),
  root("/Users/jacobbrandt/workspace/.worktrees", "worktrees"),
];

const REGION = "Folders scanned automatically";

function renderModal(scannedRoots: DiskRootSummary[], added: string[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const onClose = jest.fn();
  render(
    <DiskFoldersModal open onClose={onClose} scannedRoots={scannedRoots} added={added} />,
    { wrapper },
  );
  return { onClose };
}

describe("the automatically-scanned list's keyboard path", () => {
  it("is a list, named, and a tab stop stepping on to Close — AC3", async () => {
    renderModal(AUTOMATIC);

    // `getByRole("list")`, not "group": a <ul> keeps its native `list` role,
    // so it still announces "list, 2 items".
    const list = screen.getByRole("list", { name: REGION });
    expect(list).toHaveAttribute("tabindex", "0");
    expect(list).toHaveClass("scroll-focus-ring");

    // The footer FOLLOWS the list, so Tab forward is the step that exists;
    // nothing in or after the list is interactive until `Close`.
    list.focus();
    await userEvent.tab();
    const footerClose = screen
      .getAllByRole("button", { name: "Close" })
      .find((button) => button.textContent === "Close");
    expect(footerClose).toHaveFocus();
  });

  it("leaves the Added folders list alone, because it already has a tab stop — AC5", () => {
    renderModal(AUTOMATIC, ["/Users/jacobbrandt/.cache"]);

    // Same rule block, a different element site: every row here holds its own
    // `Remove`, so the region is reachable and must not gain a dead tab stop.
    const added = screen.getByRole("button", { name: "Stop scanning /Users/jacobbrandt/.cache" });
    const list = added.closest("ul");
    expect(list).not.toBeNull();
    expect(list).not.toHaveAttribute("tabindex");
    expect(list).not.toHaveClass("scroll-focus-ring");
  });

  it("renders no labelled list before a scan has covered anything — AC8", () => {
    renderModal([]);

    // Zero rows puts one line in its place, saying how to get some.
    expect(screen.queryByRole("list", { name: REGION })).toBeNull();
    expect(
      screen.getByText("Run a scan to list the workspaces and worktrees it covers."),
    ).toBeInTheDocument();
  });

  it("lets Escape from inside the list still close the dialog — AC9", () => {
    const { onClose } = renderModal(AUTOMATIC);

    const list = screen.getByRole("list", { name: REGION });
    list.focus();
    fireEvent.keyDown(list, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
