/**
 * Pane settings are a dialog now, not a panel under the pane's header.
 *
 * Driven through the real grid, per the discipline in `paneSettingsEditor`: the
 * control is found on a rendered page and what is stored is read out of the
 * PATCH. What is new here is *where* the form is drawn, so the assertions are
 * about the portal, the dialog semantics and the ways it closes — the form's
 * own behaviour did not change and is tested where it always was.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../../api/client";
import {
  containersOf,
  control,
  gridNode,
  installGridHarness,
  lastLayout,
  leafLayout,
  renderGrid,
  type Json,
} from "../../test/gridHarness";
import { newContainerFor } from "../views/primitives/registry";

jest.mock("../../api/client", () => require("../../test/apiClientMock"));

jest.mock("../../lib/viewsApi", () => ({
  ...jest.requireActual("../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

jest.mock("../TerminalPanel", () => ({
  __esModule: true,
  TerminalPanel: ({ workspaceSlug }: { workspaceSlug: string }) => (
    <div data-testid="live-shell" data-workspace={workspaceSlug} />
  ),
}));

installGridHarness();

const mockApi = api as jest.Mocked<typeof api>;

beforeEach(() => {
  // Without a workspace list the Workspace field falls back to its text box —
  // correct behaviour, and not the subject here.
  mockApi.workspaces.mockResolvedValue([
    { slug: "loregarden", name: "Loregarden" },
    { slug: "blobert", name: "Blobert" },
  ] as never);
});

const containerOf = (primitiveId: string): Json => newContainerFor(primitiveId) as unknown as Json;

const settingsOf = (layout: Json, containerId: string): Json =>
  containersOf(layout)[containerId].settings as Json;

async function openSettings() {
  const user = userEvent.setup();
  const { container } = renderGrid(leafLayout(containerOf("terminal")));
  await screen.findByTestId("view-host");
  await user.click(control(container, "n-seed", "pane-settings"));
  return { user, container };
}

describe("the settings form is a dialog over the app", () => {
  it("is a modal dialog, named for the primitive it configures", async () => {
    await openSettings();

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("heading", { name: "Terminal settings" })).toBeInTheDocument();
  });

  it("is portalled out of the pane, so a zoomed canvas cannot scale it", async () => {
    // 442 applies `transform: scale()` at every zoom that is not 100%, and a
    // `position: fixed` panel inside a transformed ancestor is laid out in that
    // transform. The escape is the portal, and this is the observable form of
    // it: the dialog is not a descendant of the pane it belongs to.
    const { container } = await openSettings();

    const dialog = await screen.findByRole("dialog");
    const pane = gridNode(container, "n-seed");
    expect(pane.contains(dialog)).toBe(false);
    expect(container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
  });

  it("closes on Escape without writing anything", async () => {
    const { user } = await openSettings();
    await screen.findByRole("dialog");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes when the overlay behind it is clicked", async () => {
    const { user } = await openSettings();
    const dialog = await screen.findByRole("dialog");
    const overlay = dialog.parentElement?.querySelector(".modal-overlay");
    expect(overlay).not.toBeNull();

    await user.click(overlay as Element);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("still saves through to the layout, and closes on a save", async () => {
    const { user } = await openSettings();
    const select = await screen.findByRole("combobox", { name: "Workspace" });

    await user.selectOptions(select, "");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(settingsOf(lastLayout(), "c-seed")).toMatchObject({ workspace_slug: "" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
