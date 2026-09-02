/**
 * Settings fields that name something the app can list now offer that list.
 *
 * Thirteen of the sixteen primitives' fields were free text naming a workspace
 * slug, a ticket id, an agent, a workflow or a ticket state — identifiers with
 * nothing on screen to say what the valid ones are. This file is about what an
 * operator is offered instead, and about the three ways that must not make the
 * form worse than the text box it replaced.
 *
 * The editor is driven through the real grid, per the discipline in
 * `paneSettingsEditor.test.tsx`: the control is found on a rendered page and
 * the stored value is read out of the PATCH, not out of a fixture this file
 * wrote.
 *
 * Nothing here is about how a `select` looks. jsdom has no layout engine.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { api } from "../../../api/client";
import { TICKET_STATE_LABELS } from "../../../lib/ticketStates";
import { SidebarWorkspaceProvider } from "../../../state/SidebarWorkspaceContext";
import {
  containersOf,
  control,
  installGridHarness,
  lastLayout,
  leafLayout,
  renderGrid,
  testClient,
  type Json,
} from "../../../test/gridHarness";
import { PaneSettingsEditor } from "../PaneSettingsEditor";
import { getPrimitive, newContainerFor } from "../primitives/registry";
import type { RegisteredPrimitive } from "../primitives/types";

jest.mock("../../../api/client", () => require("../../../test/apiClientMock"));

jest.mock("../../../lib/branchTriageApi", () => ({
  ...jest.requireActual("../../../lib/branchTriageApi"),
  fetchBranchTriage: jest.fn().mockResolvedValue({
    branches: [
      { name: "main", is_current: true },
      { name: "claude/wip", is_current: false },
    ],
  }),
}));

jest.mock("../../../lib/viewsApi", () => ({
  ...jest.requireActual("../../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

jest.mock("../../TerminalPanel", () => ({
  __esModule: true,
  TerminalPanel: ({ workspaceSlug }: { workspaceSlug: string }) => (
    <div data-testid="live-shell" data-workspace={workspaceSlug} />
  ),
}));

jest.mock("../../RunLedgerPanel", () => ({
  __esModule: true,
  RunLedgerPanel: ({ ticketId }: { ticketId: string }) => (
    <div data-testid="run-ledger" data-ticket={ticketId} />
  ),
}));

installGridHarness();

const mockApi = api as jest.Mocked<typeof api>;

const WORKSPACES = [
  { slug: "loregarden", name: "Loregarden" },
  { slug: "blobert", name: "Blobert" },
];

const TICKETS = [
  { id: "t-1", external_id: "lg-flex-views-433", title: "Server-side view store" },
  { id: "t-2", external_id: "lg-flex-views-557", title: "Chat primitives as panes" },
];

const containerOf = (primitiveId: string): Json => newContainerFor(primitiveId) as unknown as Json;

/**
 * The terminal's own help line, which is shown only once the list has settled.
 *
 * Waiting on "is it an input" alone is a race: the field is a text input while
 * the list is *loading* too, so the wait passes on its first poll and says
 * nothing about where the field ended up. While loading, the help is replaced
 * by "Loading the list…", so this line appearing is the settle.
 */
const WORKSPACE_HELP = "The workspace whose shell this pane opens.";

const settingsOf = (layout: Json, containerId: string): Json =>
  containersOf(layout)[containerId].settings as Json;

function primitive(id: string): RegisteredPrimitive {
  const entry = getPrimitive(id);
  if (entry === undefined) throw new Error(`no primitive ${id}`);
  return entry;
}

/**
 * Mount the editor alone, for the cases that are about one field's input.
 *
 * The grid is used wherever the claim involves what gets stored; this is for
 * the claims about what is *offered*, where a whole page is noise.
 */
function renderEditor(
  primitiveId: string,
  stored: Record<string, unknown>,
  workspaceSlug = "loregarden",
) {
  const entry = primitive(primitiveId);
  return render(
    <QueryClientProvider client={testClient()}>
      <MemoryRouter initialEntries={["/view/v-1"]}>
        <SidebarWorkspaceProvider slug={workspaceSlug}>
          <Routes>
            <Route
              path="/view/:viewId"
              element={
                <PaneSettingsEditor
                  containerId="c-1"
                  container={{ kind: entry.containerKind, settings: stored }}
                  primitive={entry}
                  onDone={() => {}}
                />
              }
            />
          </Routes>
        </SidebarWorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockApi.workspaces.mockResolvedValue(WORKSPACES as never);
  mockApi.tickets.mockResolvedValue(TICKETS as never);
  mockApi.studioAgents.mockResolvedValue([
    { slug: "backend_implementer", name: "Backend Implementer" },
  ] as never);
  mockApi.workflowTemplates.mockResolvedValue([
    { slug: "studio-loregarden-tdd-v3", name: "Loregarden TDD v3" },
  ] as never);
});

describe("a field that names something the app can list", () => {
  it("offers the workspaces instead of asking for a slug", async () => {
    renderEditor("terminal", { primitive_id: "terminal", workspace_slug: "" });

    const select = await screen.findByRole("combobox", { name: "Workspace" });
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value),
    ).toEqual(["", "loregarden", "blobert"]);
    // The label is the human name; the value is what gets stored.
    expect(within(select).getByRole("option", { name: "Loregarden" })).toHaveValue("loregarden");
  });

  it("offers ticket states without asking the server for them", async () => {
    renderEditor("chat_status_column", {
      primitive_id: "chat_status_column",
      status: "in_progress",
      ticket_ids: "",
    });

    const select = await screen.findByRole("combobox", { name: "Status" });
    expect(within(select).getByRole("option", { name: "In Progress" })).toHaveValue("in_progress");
    expect(select).toHaveValue("in_progress");
    expect(mockApi.workspaces).not.toHaveBeenCalled();
  });

  it("suggests tickets rather than listing hundreds in a dropdown", async () => {
    // A select of every ticket in a workspace is a wall, not a picker. The
    // ticket field stays a text input — an id pasted from elsewhere still
    // works — with a datalist behind it.
    const { container } = renderEditor("chat_ticket", {
      primitive_id: "chat_ticket",
      ticket_id: "",
    });

    const input = await screen.findByLabelText("Ticket");
    expect(input.tagName).toBe("INPUT");

    await waitFor(() => {
      const list = container.querySelector(`datalist#${CSS.escape(input.getAttribute("list") ?? "")}`);
      expect(list?.querySelectorAll("option")).toHaveLength(2);
    });

    const list = container.querySelector(
      `datalist#${CSS.escape(input.getAttribute("list") ?? "")}`,
    ) as HTMLDataListElement;
    // The value is the external id, not the row id: a datalist puts its `value`
    // in the box, and the running server resolves either. A UUID there is
    // something an operator can only trust; `lg-flex-views-433` is something
    // they can read.
    expect(Array.from(list.options).map((option) => option.value)).toEqual([
      "lg-flex-views-433",
      "lg-flex-views-557",
    ]);
    expect(list.options[0].label).toBe("lg-flex-views-433 · Server-side view store");
  });

  it("falls back to the row id for a ticket that has no external id", async () => {
    mockApi.tickets.mockResolvedValue([
      { id: "raw-1", external_id: "", title: "Untriaged" },
    ] as never);
    const { container } = renderEditor("chat_ticket", {
      primitive_id: "chat_ticket",
      ticket_id: "",
    });

    const input = await screen.findByLabelText("Ticket");
    await waitFor(() => {
      const list = container.querySelector(
        `datalist#${CSS.escape(input.getAttribute("list") ?? "")}`,
      ) as HTMLDataListElement | null;
      expect(Array.from(list?.options ?? []).map((option) => option.value)).toEqual(["raw-1"]);
    });
  });

  it("scopes the ticket suggestions to the workspace the view belongs to", async () => {
    renderEditor("chat_ticket", { primitive_id: "chat_ticket", ticket_id: "" }, "blobert");
    await waitFor(() => expect(mockApi.tickets).toHaveBeenCalledWith({ workspace: "blobert" }));
  });
});

describe("the list can never take the field away", () => {
  it("falls back to the text box when the list cannot be fetched", async () => {
    mockApi.workspaces.mockRejectedValue(new Error("offline"));
    renderEditor("terminal", { primitive_id: "terminal", workspace_slug: "loregarden" });

    // Both halves matter, and each caught a different wrong oracle. Waiting on
    // the settled help line first, because the field is a text input while the
    // list is loading too and a bare "is it an input" wait passes on its first
    // poll. Re-querying second, because a handle taken before the wait keeps
    // reporting INPUT after the real field has been replaced by a select.
    await screen.findByText(WORKSPACE_HELP);
    expect(screen.getByLabelText("Workspace").tagName).toBe("INPUT");
    const input = screen.getByLabelText("Workspace");
    // And it is still the operator's value, still editable.
    expect(input).toHaveValue("loregarden");
    await userEvent.clear(input);
    await userEvent.type(input, "blobert");
    expect(input).toHaveValue("blobert");
  });

  it("stays a usable text box while the list is loading, and says so", async () => {
    // Not a spinner and not disabled: an operator who already knows the slug
    // should not be made to wait for a list they were never going to read.
    mockApi.workspaces.mockReturnValue(new Promise(() => {}) as never);
    renderEditor("terminal", { primitive_id: "terminal", workspace_slug: "loregarden" });

    const input = await screen.findByLabelText("Workspace");
    expect(input.tagName).toBe("INPUT");
    expect(input).toBeEnabled();
    expect(screen.getByText("Loading the list…")).toBeInTheDocument();
    expect(screen.queryByText(WORKSPACE_HELP)).not.toBeInTheDocument();
  });

  it("falls back to the text box when the list comes back empty", async () => {
    mockApi.workspaces.mockResolvedValue([] as never);
    renderEditor("terminal", { primitive_id: "terminal", workspace_slug: "" });

    await screen.findByText(WORKSPACE_HELP);
    expect(screen.getByLabelText("Workspace").tagName).toBe("INPUT");
  });

  it("falls back to the text box when there is no workspace to scope tickets to", async () => {
    renderEditor("chat_ticket", { primitive_id: "chat_ticket", ticket_id: "t-9" }, "");

    const input = await screen.findByLabelText("Ticket");
    expect(input).toHaveValue("t-9");
    expect(input).not.toHaveAttribute("list");
    expect(mockApi.tickets).not.toHaveBeenCalled();
  });
});

describe("what gets stored", () => {
  it("writes the value the operator picked", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("terminal")));
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "pane-settings"));
    const select = await screen.findByRole("combobox", { name: "Workspace" });
    await waitFor(() => expect(within(select).queryAllByRole("option")).toHaveLength(3));
    await user.selectOptions(select, "blobert");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(settingsOf(lastLayout(), "c-seed")).toMatchObject({ workspace_slug: "blobert" }),
    );
  });

  it("keeps a stored value the list does not contain, and does not rewrite it on save", async () => {
    // The defect a bare `select` would introduce: a value with no matching
    // `option` displays as the first option instead, and the next Save writes
    // *that* over the operator's value with nobody having touched the field.
    // A view outliving a renamed workspace is exactly this case.
    const user = userEvent.setup();
    const stale = newContainerFor("terminal") as unknown as { settings: Record<string, unknown> };
    stale.settings.workspace_slug = "retired-workspace";
    const { container } = renderGrid(leafLayout(stale as unknown as Json));
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "pane-settings"));
    const select = await screen.findByRole("combobox", { name: "Workspace" });
    await waitFor(() => expect(within(select).queryAllByRole("option")).toHaveLength(4));

    expect(select).toHaveValue("retired-workspace");
    expect(
      within(select).getByRole("option", { name: /retired-workspace \(not in this list\)/ }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(settingsOf(lastLayout(), "c-seed")).toMatchObject({
        workspace_slug: "retired-workspace",
      }),
    );
  });

  it("can still be cleared back to the primitive's own empty state", async () => {
    const user = userEvent.setup();
    const configured = newContainerFor("terminal") as unknown as {
      settings: Record<string, unknown>;
    };
    configured.settings.workspace_slug = "loregarden";
    const { container } = renderGrid(leafLayout(configured as unknown as Json));
    await screen.findByTestId("view-host");

    await user.click(control(container, "n-seed", "pane-settings"));
    const select = await screen.findByRole("combobox", { name: "Workspace" });
    await waitFor(() => expect(within(select).queryAllByRole("option")).toHaveLength(3));
    await user.selectOptions(select, "");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(settingsOf(lastLayout(), "c-seed")).toMatchObject({ workspace_slug: "" }),
    );
  });
});

describe("branch fields", () => {
  it("offers the workspace's branches, marking the current one", async () => {
    // Left as free text when every other identifier became a picker, because
    // the branch list lives in `branchTriageApi` rather than the main client —
    // which is a reason to import it, not a reason to make an operator type a
    // branch name from memory.
    renderEditor("chat_branch_history", {
      primitive_id: "chat_branch_history",
      workspace_slug: "loregarden",
      branch: "",
      limit: 8,
    });

    const select = await screen.findByRole("combobox", { name: "Branch" });
    expect(within(select).getByRole("option", { name: "main · current" })).toHaveValue("main");
    expect(within(select).getByRole("option", { name: "claude/wip" })).toHaveValue("claude/wip");
  });
});

describe("the list fields that stayed text", () => {
  it("name the states they accept, from the vocabulary itself", async () => {
    // `statuses` and `filters` are comma-separated strings — `SettingsField`
    // has no list kind — so they are the only fields left asking for values
    // with nothing on screen naming them. The help line spells the states out,
    // and is derived rather than typed, so this fails if a state is added and
    // the sentence is not.
    renderEditor("chat_filterable_kanban", {
      primitive_id: "chat_filterable_kanban",
      statuses: "",
      filters: "",
      ticket_ids: "",
    });

    const help = await screen.findAllByText(/One or more of/);
    expect(help).toHaveLength(2);
    for (const state of Object.keys(TICKET_STATE_LABELS)) {
      expect(help[0]).toHaveTextContent(state);
    }
  });
});
