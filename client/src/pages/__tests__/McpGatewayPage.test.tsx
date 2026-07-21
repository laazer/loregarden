import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import { McpGatewayPage } from "../McpGatewayPage";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function server(overrides = {}) {
  return {
    id: "s1",
    name: "github",
    description: "",
    transport: "http",
    url: "https://mcp.example/sse",
    command: "",
    args: [],
    auth_env_var: "",
    auth_present: false,
    enabled: true,
    created_at: "2026-07-20T00:00:00",
    updated_at: "2026-07-20T00:00:00",
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <McpGatewayPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.mcpServers.mockResolvedValue([]);
});

it("says agents still have loregarden when nothing is registered", async () => {
  renderPage();
  expect(await screen.findByText(/no servers registered/i)).toBeInTheDocument();
  // The built-in server is not a registry row but is always reachable.
  expect(screen.getByText("loregarden")).toBeInTheDocument();
});

it("lists a registered server with its transport", async () => {
  mockApi.mcpServers.mockResolvedValue([server()] as never);

  renderPage();
  expect(await screen.findByText("github")).toBeInTheDocument();
  expect(screen.getByText(/https:\/\/mcp\.example\/sse/)).toBeInTheDocument();
});

it("shows a credential as missing without ever holding the value", async () => {
  // The server reports presence only; the page can say "missing" because it
  // was told so, not because it read anything.
  mockApi.mcpServers.mockResolvedValue([
    server({ auth_env_var: "GITHUB_MCP_TOKEN", auth_present: false }),
  ] as never);

  renderPage();
  expect(await screen.findByText(/GITHUB_MCP_TOKEN · missing/)).toBeInTheDocument();
});

it("registers a server through the form", async () => {
  mockApi.createMcpServer.mockResolvedValue(server() as never);

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /add server/i }));
  fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: "linear" } });
  fireEvent.change(screen.getByLabelText(/^url$/i), {
    target: { value: "https://mcp.linear.app/sse" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^register server$/i }));

  await waitFor(() => expect(mockApi.createMcpServer).toHaveBeenCalled());
  expect(mockApi.createMcpServer.mock.calls[0][0]).toMatchObject({
    name: "linear",
    transport: "http",
    url: "https://mcp.linear.app/sse",
  });
});

it("asks for a command instead of a url for a stdio server", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /add server/i }));
  fireEvent.change(screen.getByLabelText(/transport/i), { target: { value: "stdio" } });

  expect(screen.getByLabelText(/command/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/^url$/i)).not.toBeInTheDocument();
});

it("surfaces a rejected registration rather than failing silently", async () => {
  mockApi.createMcpServer.mockRejectedValue(new Error("An http server needs a url"));

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /add server/i }));
  fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: "broken" } });
  fireEvent.click(screen.getByRole("button", { name: /^register server$/i }));

  expect(await screen.findByText(/needs a url/i)).toBeInTheDocument();
});

it("tells the operator the field wants a variable name, not a token", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /add server/i }));
  expect(screen.getByText(/name, not its value/i)).toBeInTheDocument();
});
