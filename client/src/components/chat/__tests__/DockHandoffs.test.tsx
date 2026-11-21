import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api, ApiError, type HandoffFeed } from "../../../api/client";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import { useUiStore } from "../../../state/uiStore";
import workedTicket from "../../../test/fixtures/prod-shape/handoffs-worked-ticket.json";
import workspaceFeed from "../../../test/fixtures/prod-shape/handoffs-loregarden.json";
import { DockHandoffs } from "../DockHandoffs";

jest.mock("../../../api/client", () => {
  const actual = jest.requireActual("../../../api/client");
  return { ...actual, api: { ...actual.api, handoffs: jest.fn(), workspaces: jest.fn().mockResolvedValue([]) } };
});
const mockHandoffs = api.handoffs as jest.MockedFunction<typeof api.handoffs>;

const chain = workedTicket as unknown as HandoffFeed;
const feed = workspaceFeed as unknown as HandoffFeed;

function renderTab(props: { ticketId: string | null }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DockHandoffs {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  // The thread answers for loregarden while the page shows every workspace.
  useUiStore.setState({ chatScope: "loregarden", workspace: "all" });
});

describe("on a ticket", () => {
  it("lists the ticket's chain over the scenario, usable", async () => {
    mockHandoffs.mockResolvedValue(chain);
    const { container } = renderTab({ ticketId: "t1" });

    const list = await screen.findByRole("list", { name: "Handoffs between agents, newest first" });
    expect(mockHandoffs).toHaveBeenCalledWith({ ticketId: "t1", q: "" });
    expect(within(list).getAllByRole("listitem", { hidden: false })).toHaveLength(chain.items.length);
    // On its own ticket a row does not link back to the page it is on.
    expect(within(list).queryAllByRole("link")).toHaveLength(0);
    expect(findUsabilityProblems(container)).toEqual([]);
  });

  it("opens a handoff to the checklist and evidence that went with it", async () => {
    mockHandoffs.mockResolvedValue(chain);
    renderTab({ ticketId: "t1" });

    const first = chain.items[0];
    const [toggle] = await screen.findAllByRole("button", { name: /Show checklist/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const detail = document.getElementById(toggle.getAttribute("aria-controls") ?? "");
    expect(detail).not.toBeNull();
    for (const item of first.checklist) {
      expect(detail).toHaveTextContent(item.item);
    }
  });

  it("marks a handoff that fell short of its required items apart from one that met them", async () => {
    const short = chain.items.find(
      (entry) => entry.required_items_met !== null && entry.required_items_met < (entry.total_required_items ?? 0),
    );
    expect(short).toBeDefined();
    mockHandoffs.mockResolvedValue({ items: [short!], total: 1 });
    renderTab({ ticketId: "t1" });

    const chip = await screen.findByText(`${short!.required_items_met}/${short!.total_required_items} required met`);
    expect(chip).toHaveAttribute("data-tone", "wait");
  });
});

describe("on a workspace", () => {
  it("names each row's ticket as a link to it, and says when it shows only the newest", async () => {
    mockHandoffs.mockResolvedValue({ ...feed, total: 1129 });
    renderTab({ ticketId: null });

    expect(await screen.findByText(`Newest ${feed.items.length} of 1,129`)).toBeInTheDocument();
    expect(mockHandoffs).toHaveBeenCalledWith({ workspace: "loregarden", q: "" });
    const link = screen.getAllByRole("link")[0];
    expect(link).toHaveAttribute("href", expect.stringContaining(`/tickets/${feed.items[0].ticket_id}`));
  });

  it("lists an unreadable handoff in its place, with the reason one click away", async () => {
    const unreadable = { ...feed.items[0], from_agent: "", to_agent: "", checklist: [], unreadable: "not a handoff document: Field required" };
    mockHandoffs.mockResolvedValue({ items: [unreadable], total: 1 });
    renderTab({ ticketId: null });

    expect(await screen.findByText("Unreadable handoff")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show details" }));
    expect(screen.getByText(unreadable.unreadable)).toBeInTheDocument();
  });
});

describe("the filter", () => {
  const narrowed = (q: string): HandoffFeed => {
    const items = feed.items.filter((entry) => entry.to_agent === q || entry.from_agent === q);
    return { items, total: items.length };
  };

  it("searches the whole workspace on the server, not the page already loaded", async () => {
    mockHandoffs.mockImplementation(async ({ q }) => (q ? narrowed(q) : { ...feed, total: 1129 }));
    renderTab({ ticketId: null });
    const box = await screen.findByLabelText("Filter handoffs by agent or ticket");

    const agent = feed.items[0].to_agent;
    fireEvent.change(box, { target: { value: `  ${agent} ` } });
    // Typed, not yet sent: the count says so rather than describing the old page.
    expect(screen.getByText("Searching…")).toBeInTheDocument();

    const expected = narrowed(agent);
    expect(
      await screen.findByText(`${expected.total} ${expected.total === 1 ? "handoff" : "handoffs"} matching`),
    ).toBeInTheDocument();
    expect(mockHandoffs).toHaveBeenLastCalledWith({ workspace: "loregarden", q: agent });
    const list = screen.getByRole("list", { name: "Handoffs between agents, newest first" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(expected.items.length);
    // Fewer results than the threshold leave the box where it is.
    expect(screen.getByLabelText("Filter handoffs by agent or ticket")).toHaveValue(`  ${agent} `);
  });

  it("says nothing matched in this workspace, keeping the search to change", async () => {
    mockHandoffs.mockImplementation(async ({ q }) => (q ? { items: [], total: 0 } : { ...feed, total: 1129 }));
    renderTab({ ticketId: null });

    fireEvent.change(await screen.findByLabelText("Filter handoffs by agent or ticket"), {
      target: { value: "nobody" },
    });
    expect(await screen.findByText("No handoff in loregarden matches “nobody”.")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter handoffs by agent or ticket")).toHaveValue("nobody");
  });

  it("never shows the last ticket's handoffs while the next ticket's load", async () => {
    mockHandoffs.mockImplementation(({ ticketId }) => (ticketId === "t1" ? Promise.resolve(chain) : new Promise(() => {})));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tab = (ticketId: string) => (
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DockHandoffs ticketId={ticketId} />
        </MemoryRouter>
      </QueryClientProvider>
    );
    const { rerender } = render(tab("t1"));
    await screen.findByRole("list", { name: "Handoffs between agents, newest first" });

    rerender(tab("t2"));
    expect(screen.queryByRole("list", { name: "Handoffs between agents, newest first" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("offers no filter where there is too little to narrow", async () => {
    mockHandoffs.mockResolvedValue({ items: chain.items.slice(0, 3), total: 3 });
    renderTab({ ticketId: "t1" });
    expect(await screen.findByText("3 handoffs")).toBeInTheDocument();
    expect(screen.queryByLabelText("Filter handoffs by agent or ticket")).not.toBeInTheDocument();
  });
});

describe("the other states", () => {
  it("shows the list's shape while it loads", () => {
    mockHandoffs.mockReturnValue(new Promise(() => {}));
    renderTab({ ticketId: "t1" });
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("says what would appear when there are none, not a blank pane", async () => {
    mockHandoffs.mockResolvedValue({ items: [], total: 0 });
    renderTab({ ticketId: "t1" });
    expect(await screen.findByText(/No handoffs on this ticket yet/)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("names the failure and retries on request, rather than looking empty", async () => {
    mockHandoffs.mockRejectedValueOnce(new ApiError(500, "database is locked")).mockResolvedValue(chain);
    renderTab({ ticketId: "t1" });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("database is locked");
    expect(screen.queryByText(/No handoffs/)).not.toBeInTheDocument();
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("list", { name: "Handoffs between agents, newest first" })).toBeInTheDocument();
  });
});
