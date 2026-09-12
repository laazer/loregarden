/**
 * The two panels that were on `excludedPanels` and are now panes.
 *
 * Both are exercised through `ContainerPrimitiveHost`, for the reason
 * `containerPrimitives.render.test` gives: the host is the only path a view
 * has, and it is where `parseSettings` runs. Neither panel is mocked away —
 * a test that stubs `LogsPanel` proves the primitive renders *a* component,
 * not that it renders a log.
 *
 * What is asserted is behaviour, not copy. The one exception is the workspace
 * name inside the scoped empty state, which is not decoration: without it,
 * "nothing here" and "your filter matched nothing" are the same sentence.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../../../api/client";
import { ContainerPrimitiveHost } from "../primitives/registry";

jest.mock("../../../api/client", () => require("../../../test/apiClientMock"));

const mockApi = api as jest.Mocked<typeof api>;

function renderHost(settings: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    ...render(
      <QueryClientProvider client={client}>
        <ContainerPrimitiveHost containerId="c1" settings={settings} />
      </QueryClientProvider>,
    ),
    client,
  };
}

/**
 * Wait until the approvals fetch has actually landed in the cache.
 *
 * Without this, an assertion on the empty state is a wrong oracle: the list is
 * empty *before* the fetch resolves too, so `findByText("No pending approvals…")`
 * passes on the very first poll and says nothing about the filter. It was
 * written that way first, and survived a control run that disabled the filter
 * entirely — which is how it was caught.
 */
async function approvalsLoaded(client: QueryClient) {
  await waitFor(() => {
    expect(client.getQueryData(["approvals"])).toBeDefined();
  });
}

const ticketDetail = (overrides: Record<string, unknown> = {}) => ({
  id: "t-1",
  external_id: "lg-flex-views-561",
  title: "A ticket",
  workspace_slug: "blobert",
  state: "in_progress",
  artifacts: { logs: [{ time: "12:00:00", tag: "impl", text: "hello from the run" }], live: null },
  ...overrides,
});

const approval = (id: string, workspaceSlug: string) => ({
  id,
  title: `Gate ${id}`,
  level: "stage",
  workspace_slug: workspaceSlug,
  stage_key: "verify",
  stage_name: "Verify",
  impact: "",
  ticket_id: `ticket-${id}`,
  ticket_external_id: `ext-${id}`,
  kind: "workflow_gate",
  run_id: `run-${id}`,
  tool_name: "",
  tool_input_json: "",
  cli_adapter: "claude",
});

beforeEach(() => {
  // Cleared, not just re-stubbed: the last test counts calls, and a mock
  // carrying the previous tests' calls turns that assertion into noise.
  jest.clearAllMocks();
  mockApi.ticketLedger.mockResolvedValue({
    visits: [],
    total_runs: 0,
    reworked_stages: [],
    total_seconds: 0,
  } as never);
  mockApi.approvals.mockResolvedValue([] as never);
});

describe("the Run Log pane", () => {
  it("says what it is waiting for rather than fetching a ticket that cannot exist", () => {
    renderHost({ primitive_id: "ticket_logs" });
    // `api.ticket("")` would 404 on every poll for a pane just dropped in.
    expect(mockApi.ticket).not.toHaveBeenCalled();
    const host = document.querySelector("[data-container-id='c1']");
    expect(host?.textContent?.trim().length).toBeGreaterThan(0);
  });

  it("turns the ticket id into the detail the panel needs, and draws the log", async () => {
    // The whole of the blocker the exclusion list recorded: the panel wants a
    // TicketDetail, a container can only store an id, and `api.ticket` is the
    // step between them.
    mockApi.ticket.mockResolvedValue(ticketDetail() as never);
    renderHost({ primitive_id: "ticket_logs", ticket_id: "t-1" });

    await waitFor(() => expect(mockApi.ticket).toHaveBeenCalledWith("t-1"));
    expect(await screen.findByText("hello from the run")).toBeInTheDocument();
  });

  it("renders the panel's own empty state for a ticket with no lines yet", async () => {
    // Two empties kept apart: no ticket chosen is the pane's, no output yet is
    // the panel's, and collapsing them would tell an operator to go configure a
    // pane that is already configured correctly.
    mockApi.ticket.mockResolvedValue(ticketDetail({ artifacts: { logs: [], live: null } }) as never);
    renderHost({ primitive_id: "ticket_logs", ticket_id: "t-1" });

    await waitFor(() => expect(mockApi.ticket).toHaveBeenCalledWith("t-1"));
    expect(await screen.findByText(/No log lines yet/)).toBeInTheDocument();
  });

  it("stays standing when the ticket cannot be fetched, and names the id", async () => {
    // A blank pane and a broken one are indistinguishable, and a view outlives
    // the tickets it points at — so the id is what makes the state actionable.
    mockApi.ticket.mockRejectedValue(new Error("offline"));
    renderHost({ primitive_id: "ticket_logs", ticket_id: "t-gone" });

    await waitFor(() => expect(mockApi.ticket).toHaveBeenCalledWith("t-gone"));
    expect(await screen.findByText(/t-gone/)).toBeInTheDocument();
  });
});

describe("the Approvals pane", () => {
  it("shows every workspace's approvals when it names none", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("a", "loregarden"),
      approval("b", "blobert"),
    ] as never);
    renderHost({ primitive_id: "approvals", workspace_slug: "" });

    expect(await screen.findByText("Gate a")).toBeInTheDocument();
    expect(screen.getByText("Gate b")).toBeInTheDocument();
  });

  it("narrows to the workspace it names", async () => {
    // The point of the whole change: two of these panes in one tab, each about
    // a different workspace. The endpoint has no workspace filter, so the
    // narrowing is here and has to actually happen.
    mockApi.approvals.mockResolvedValue([
      approval("a", "loregarden"),
      approval("b", "blobert"),
    ] as never);
    renderHost({ primitive_id: "approvals", workspace_slug: "blobert" });

    expect(await screen.findByText("Gate b")).toBeInTheDocument();
    expect(screen.queryByText("Gate a")).not.toBeInTheDocument();
  });

  it("names the workspace in its empty state, so a filter cannot read as a fault", async () => {
    mockApi.approvals.mockResolvedValue([approval("a", "loregarden")] as never);
    const { client } = renderHost({ primitive_id: "approvals", workspace_slug: "blobert" });

    // The approval exists and was fetched — it is this pane's scope that
    // excludes it. Asserting before the fetch lands would assert the loading
    // state instead, which looks identical and means nothing.
    await approvalsLoaded(client);
    expect(screen.queryByText("Gate a")).not.toBeInTheDocument();
    expect(screen.getByText(/No pending approvals in blobert/)).toBeInTheDocument();
  });

  it("offers no ticket navigation, because a container has no route", async () => {
    // The same rule the run ledger follows. An Inspect button that cannot
    // navigate is worse than no button: it is a control that does nothing.
    mockApi.approvals.mockResolvedValue([approval("a", "loregarden")] as never);
    renderHost({ primitive_id: "approvals", workspace_slug: "" });

    await screen.findByText("Gate a");
    expect(screen.queryByRole("button", { name: /Approvals tab|Inspect/ })).not.toBeInTheDocument();
  });
});

/**
 * The boards, which fetch a bucket of tickets rather than resolving one id.
 *
 * These were the last surfaces that could not be pointed anywhere: with no
 * `ticket_ids` they called `api.tickets({})` and `api.ticketTree({})` — every
 * workspace, with no way to say otherwise. A superset is not the same as an
 * answer, and two boards about two workspaces was the thing being asked for.
 *
 * The scope applies to the *fallback* only. An explicit id list is explicit.
 */
describe("the board panes draw from the workspace they name", () => {
  it("scopes a kanban's ticket fetch", async () => {
    mockApi.tickets.mockResolvedValue([] as never);
    renderHost({ primitive_id: "chat_kanban", workspace_slug: "blobert", statuses: "", ticket_ids: "" });

    await waitFor(() => expect(mockApi.tickets).toHaveBeenCalledWith({ workspace: "blobert" }));
  });

  it("scopes a status column the same way", async () => {
    mockApi.tickets.mockResolvedValue([] as never);
    renderHost({
      primitive_id: "chat_status_column",
      workspace_slug: "blobert",
      status: "in_progress",
      ticket_ids: "",
    });

    await waitFor(() => expect(mockApi.tickets).toHaveBeenCalledWith({ workspace: "blobert" }));
  });

  it("scopes a ticket list's whole-tree fallback", async () => {
    mockApi.ticketTree.mockResolvedValue([] as never);
    renderHost({
      primitive_id: "chat_ticket_list",
      workspace_slug: "blobert",
      parent_ticket_id: "",
      ticket_ids: "",
    });

    await waitFor(() => expect(mockApi.ticketTree).toHaveBeenCalledWith({ workspace: "blobert" }));
  });

  it("asks for every workspace when it names none", async () => {
    // The behaviour every board had before the field existed, and the behaviour
    // every board stored before it still has: `""` is "not narrowed", not
    // "no workspace".
    mockApi.tickets.mockResolvedValue([] as never);
    renderHost({ primitive_id: "chat_kanban", workspace_slug: "", statuses: "", ticket_ids: "" });

    await waitFor(() => expect(mockApi.tickets).toHaveBeenCalledWith({}));
  });

  it("fetches a named ticket whatever workspace it lives in", async () => {
    // The scope narrows the fallback, never an explicit list. Dropping a named
    // ticket because it lives elsewhere would make the board disagree with the
    // settings that asked for it — and do so silently.
    mockApi.ticket.mockResolvedValue(ticketDetail({ id: "t-1", workspace_slug: "elsewhere" }) as never);
    renderHost({
      primitive_id: "chat_kanban",
      workspace_slug: "blobert",
      statuses: "",
      ticket_ids: "t-1",
    });

    await waitFor(() => expect(mockApi.ticket).toHaveBeenCalledWith("t-1"));
    expect(mockApi.tickets).not.toHaveBeenCalled();
  });
});

/**
 * The headline case, stated once and directly.
 *
 * Everything above is a piece of it: the pickers stopped reading the chrome,
 * the panels that needed a seam got one. What was asked for is a *tab* holding
 * panes about more than one workspace, and that is a claim about two panes side
 * by side, not about either one alone.
 */
describe("one tab, two workspaces", () => {
  it("renders two approvals panes scoped to different workspaces at once", async () => {
    mockApi.approvals.mockResolvedValue([
      approval("a", "loregarden"),
      approval("b", "blobert"),
    ] as never);

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        {/* One client, as a view has: the two panes share the fetch and
            disagree only about what to show from it. */}
        <ContainerPrimitiveHost
          containerId="left"
          settings={{ primitive_id: "approvals", workspace_slug: "loregarden" }}
        />
        <ContainerPrimitiveHost
          containerId="right"
          settings={{ primitive_id: "approvals", workspace_slug: "blobert" }}
        />
      </QueryClientProvider>,
    );

    await approvalsLoaded(client);
    const left = document.querySelector("[data-container-id='left']") as HTMLElement;
    const right = document.querySelector("[data-container-id='right']") as HTMLElement;

    expect(within(left).getByText("Gate a")).toBeInTheDocument();
    expect(within(left).queryByText("Gate b")).not.toBeInTheDocument();
    expect(within(right).getByText("Gate b")).toBeInTheDocument();
    expect(within(right).queryByText("Gate a")).not.toBeInTheDocument();

    // One request, not two: the panes differ in what they filter to, not in
    // what they ask the server for.
    expect(mockApi.approvals).toHaveBeenCalledTimes(1);
  });
});
