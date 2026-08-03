import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import { TopbarPageSlot, TopbarPageSlotProvider } from "../../components/TopbarPageSlot";
import { mcpServer, mcpTelemetry, studioAgent } from "../../test/mcpFixtures";
import { McpGatewayPage } from "../McpGatewayPage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        {/* The page's title and its "Register server" control live in the topbar. */}
        <TopbarPageSlotProvider>
          <TopbarPageSlot />
          <McpGatewayPage />
        </TopbarPageSlotProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openRegisterModal() {
  fireEvent.click(await screen.findByRole("button", { name: /register server/i }));
  return screen.findByRole("dialog");
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.mcpServers.mockResolvedValue([]);
  mockApi.mcpTelemetry.mockResolvedValue(mcpTelemetry());
  mockApi.studioAgents.mockResolvedValue([]);
  mockApi.mcpPolicy.mockResolvedValue({ auto_approved: [], orchestrated_denied: [] });
});

it("says agents still have loregarden when nothing is registered", async () => {
  renderPage();
  expect(await screen.findByText(/no servers registered/i)).toBeInTheDocument();
  // The built-in server is not a registry row but is always reachable.
  expect(screen.getAllByText("loregarden").length).toBeGreaterThan(0);
});

it("lists a registered server with its transport", async () => {
  mockApi.mcpServers.mockResolvedValue([mcpServer()]);

  renderPage();
  const rail = within(await screen.findByTestId("mcp-registry-rail"));
  expect(await rail.findByText("github")).toBeInTheDocument();
  expect(rail.getByText(/https:\/\/mcp\.example\/sse/)).toBeInTheDocument();
  expect(rail.getByText("http")).toBeInTheDocument();
});

it("shows a credential as missing without ever holding the value", async () => {
  // The server reports presence only; the page can say "missing" because it
  // was told so, not because it read anything.
  mockApi.mcpServers.mockResolvedValue([
    mcpServer({ auth_env_var: "GITHUB_MCP_TOKEN", auth_present: false }),
  ]);

  renderPage();
  expect(await screen.findByText(/GITHUB_MCP_TOKEN · missing/)).toBeInTheDocument();
});

it("registers a server through the modal", async () => {
  mockApi.createMcpServer.mockResolvedValue(mcpServer());

  renderPage();
  const dialog = await openRegisterModal();
  fireEvent.change(within(dialog).getByLabelText(/^name$/i), { target: { value: "linear" } });
  fireEvent.change(within(dialog).getByLabelText(/^url$/i), {
    target: { value: "https://mcp.linear.app/sse" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: /^register server$/i }));

  await waitFor(() => expect(mockApi.createMcpServer).toHaveBeenCalled());
  expect(mockApi.createMcpServer.mock.calls[0][0]).toMatchObject({
    name: "linear",
    transport: "http",
    url: "https://mcp.linear.app/sse",
  });
});

it("asks for a command instead of a url for a stdio server", async () => {
  renderPage();
  const dialog = await openRegisterModal();
  fireEvent.change(within(dialog).getByLabelText(/transport/i), { target: { value: "stdio" } });

  expect(within(dialog).getByLabelText(/command/i)).toBeInTheDocument();
  expect(within(dialog).queryByLabelText(/^url$/i)).not.toBeInTheDocument();
});

it("surfaces a rejected registration rather than failing silently", async () => {
  mockApi.createMcpServer.mockRejectedValue(new Error("An http server needs a url"));

  renderPage();
  const dialog = await openRegisterModal();
  fireEvent.change(within(dialog).getByLabelText(/^name$/i), { target: { value: "broken" } });
  fireEvent.click(within(dialog).getByRole("button", { name: /^register server$/i }));

  expect(await screen.findByText(/needs a url/i)).toBeInTheDocument();
});

it("tells the operator the field wants a variable name, not a token", async () => {
  renderPage();
  const dialog = await openRegisterModal();
  expect(within(dialog).getByText(/name, not its value/i)).toBeInTheDocument();
});

it("marks a trusted server, since that is the security-relevant state", async () => {
  mockApi.mcpServers.mockResolvedValue([mcpServer({ tool_policy: "auto" })]);

  renderPage();
  expect(await screen.findByText("trusted")).toBeInTheDocument();
});

it("defaults a new server to asking every time", async () => {
  renderPage();
  const dialog = await openRegisterModal();

  // Registering grants reach, not trust.
  const policy = within(dialog).getByLabelText(/when an agent calls this server/i);
  expect((policy as HTMLSelectElement).value).toBe("prompt");
});

it("says tools are not listed rather than showing zero", async () => {
  // A server nobody has checked has an unknown catalogue. "0 tools" is a claim
  // the operator would act on.
  mockApi.mcpServers.mockResolvedValue([mcpServer()]);

  renderPage();
  expect(await screen.findByText("tools not listed")).toBeInTheDocument();
});

it("shows the tools a check actually found", async () => {
  mockApi.mcpServers.mockResolvedValue([
    mcpServer({
      tools: ["create_issue", "list_repos"],
      tools_listed_at: "2026-07-20T10:00:00",
      last_checked_at: "2026-07-20T10:00:00",
      last_health_ok: true,
    }),
  ]);

  renderPage();
  fireEvent.click(await screen.findByText("2 tools"));
  const detail = within(await screen.findByTestId("mcp-server-detail"));
  expect(await detail.findByText("create_issue")).toBeInTheDocument();
  expect(detail.getByText("list_repos")).toBeInTheDocument();
});

it("states that reach is not per-agent rather than implying grants", async () => {
  // There is no per-agent grant for a registered server, so the rules table
  // must not suggest one can be revoked here.
  mockApi.mcpServers.mockResolvedValue([mcpServer()]);
  mockApi.studioAgents.mockResolvedValue([studioAgent()]);

  renderPage();
  expect(await screen.findByText("Every agent")).toBeInTheDocument();
});

it("keeps the rules table scrollable and labelled with its size", async () => {
  // The table outgrows the viewport once a registry has real agents in it.
  // Letting it grow pushed the switchboard off the top of the column, so it
  // scrolls itself — which only works if it stays reachable and countable.
  mockApi.mcpServers.mockResolvedValue([mcpServer()]);
  mockApi.studioAgents.mockResolvedValue([
    studioAgent({ slug: "a", name: "Planner" }),
    studioAgent({ slug: "b", name: "Reviewer" }),
  ]);

  renderPage();

  // Two agent rows plus one server row.
  const table = await screen.findByRole("table", { name: /routing rules, 3 rules/i });
  // Keyboard users need the region focusable; it holds nothing focusable itself.
  expect(table).toHaveAttribute("tabindex", "0");
  expect(await screen.findByText("3")).toBeInTheDocument();
});

it("reports an agent's loregarden grant as partly unattended, not wholly", async () => {
  // Reads and bookkeeping writes are allowlisted; workflow-state writes still
  // stop for a human. Labelling the whole grant "allowlisted" would overstate
  // what runs on its own.
  mockApi.studioAgents.mockResolvedValue([
    studioAgent({ mcp_tools: ["loregarden_get_ticket", "loregarden_complete_stage"] }),
  ]);
  mockApi.mcpPolicy.mockResolvedValue({
    auto_approved: ["loregarden_get_ticket"],
    orchestrated_denied: [],
  });

  renderPage();
  expect(await screen.findByText("1/2 auto")).toBeInTheDocument();
});

it("shows a rate over the window it was measured on", async () => {
  mockApi.mcpServers.mockResolvedValue([mcpServer()]);
  mockApi.mcpTelemetry.mockResolvedValue(
    mcpTelemetry({
      by_server: { github: 120 },
      calls_per_min: 2,
      per_server: {
        github: {
          calls: 120,
          calls_in_window: 120,
          calls_per_min: 2,
          window_minutes: 60,
          agent_ids: ["planner"],
          last_call_at: "2026-07-20T10:00:00",
        },
      },
    }),
  );

  renderPage();
  expect(await screen.findByText("2/min")).toBeInTheDocument();
});

it("edits an existing server through the same modal", async () => {
  mockApi.mcpServers.mockResolvedValue([mcpServer()]);
  mockApi.updateMcpServer.mockResolvedValue(mcpServer({ description: "Repos and issues" }));

  renderPage();
  const rail = within(await screen.findByTestId("mcp-registry-rail"));
  fireEvent.click(await rail.findByText("github"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText(/what it is for/i), {
    target: { value: "Repos and issues" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: /save changes/i }));

  await waitFor(() => expect(mockApi.updateMcpServer).toHaveBeenCalled());
  expect(mockApi.updateMcpServer.mock.calls[0][1]).toMatchObject({
    description: "Repos and issues",
  });
});
