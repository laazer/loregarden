import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { AppLayout } from "../AppLayout";
import { api } from "../../api/client";
import { useUiStore } from "../../state/uiStore";

// The sidebar reads the view store; these tests are about the topbar and dock,
// and their api mock knows nothing about views. Its props are recorded, because
// which workspace the layout hands it is a decision made here.
const mockSidebarProps: Array<{ workspaceSlug: string }> = [];
jest.mock("../AppSidebar", () => ({
  AppSidebar: (props: { workspaceSlug: string }) => {
    mockSidebarProps.push(props);
    return <div data-testid="app-sidebar" />;
  },
}));

jest.mock("../CopilotDock", () => ({
  CopilotDock: () => <div data-testid="copilot-dock" />,
}));

jest.mock("../AppTopbarActions", () => ({
  AppTopbarActions: () => <div data-testid="topbar-actions" />,
}));

jest.mock("../SettingsModal", () => ({
  SettingsModal: () => null,
}));

jest.mock("../QueueNotificationsHost", () => ({
  QueueNotificationsHost: () => null,
}));

jest.mock("../../api/client", () => ({
  // The action bar's Baxter fallback narrows load failures with `instanceof
  // ApiError`, which throws when the mock omits the class.
  ApiError: class ApiError extends Error {},
  api: {
    workspaces: jest.fn(async () => []),
    baxterChatSession: jest.fn(async () => ({
      messages: [],
      runtime: {
        cli_adapter: "default",
        claude_model: "",
        cursor_model: "",
        codex_model: "",
        lmstudio_base_url: "",
        lmstudio_model: "",
        claude_effort: "",
        cursor_effort: "",
        lmstudio_effort: "",
      },
      run_status: "idle",
    })),
    runtimeOptions: jest.fn(async () => ({})),
    approvals: jest.fn(async () => []),
    usage: jest.fn(async () => null),
    ticket: jest.fn(async () => null),
  },
}));

function wrap(ui: ReactNode, path: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<AppLayout>{ui}</AppLayout>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const mockedApi = api as jest.Mocked<typeof api>;

beforeEach(() => {
  jest.clearAllMocks();
  mockSidebarProps.length = 0;
  mockedApi.workspaces.mockResolvedValue([]);
  useUiStore.setState({
    utilityDockEdge: "bottom",
    terminalOpen: false,
    workspace: "all",
    baxterHistoryOpen: false,
    chatWorkspaceSlug: "",
  });
});

it("shows topbar and utility dock on console", () => {
  wrap(<div>console body</div>, "/console");
  expect(screen.getByText("loregarden")).toBeInTheDocument();
  expect(screen.getByTestId("topbar-actions")).toBeInTheDocument();
  expect(screen.getByTestId("copilot-dock")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Dock utility panel to the right/i })).toBeInTheDocument();
});

it("keeps the utility dock on chat too, for the tools that are not the chat", () => {
  // The shell and the dock control are screen-level; a screen losing them
  // because it happens to be the chat page is the bug this replaced.
  wrap(<div>chat body</div>, "/chat");
  expect(screen.getByText("loregarden")).toBeInTheDocument();
  expect(screen.getByTestId("topbar-actions")).toBeInTheDocument();
  expect(screen.getByText("Agent SDLC · Chat")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /History/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /New chat/i })).toBeInTheDocument();
  expect(screen.getByTestId("copilot-dock")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Terminal" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /Dock utility panel to the right/i }),
  ).toBeInTheDocument();
});

it("leaves the composer to the chat page, which draws its own", () => {
  wrap(<div>chat body</div>, "/chat");
  expect(screen.queryByLabelText("Message this conversation")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
});

it("names the chat workspace in the topbar without re-filtering the Console", async () => {
  mockedApi.workspaces.mockResolvedValue([
    { slug: "loregarden", name: "Loregarden" },
    { slug: "blobert", name: "Blobert" },
  ] as never);

  wrap(<div>chat body</div>, "/chat");

  const picker = await screen.findByLabelText("Chat workspace");
  await waitFor(() => expect(picker).toHaveValue("loregarden"));

  fireEvent.change(picker, { target: { value: "blobert" } });
  expect(useUiStore.getState().chatWorkspaceSlug).toBe("blobert");
  // Home and the Console keep their own filter — confining chat must not
  // confine them too.
  expect(useUiStore.getState().workspace).toBe("all");
});

it("keeps the chat workspace picker off non-chat pages", () => {
  wrap(<div>console body</div>, "/console");
  expect(screen.queryByLabelText("Chat workspace")).not.toBeInTheDocument();
});

it("keeps the sidebar's workspace off the route, and off the page-scoped slugs", async () => {
  // The settings modal resolves a workspace through the current page; the
  // sidebar must not. Walking `/queue` → `/console` would otherwise swap the
  // entire tab set out from under the user on a navigation that says nothing
  // about workspaces.
  mockedApi.workspaces.mockResolvedValue([
    { slug: "loregarden", name: "Loregarden" },
    { slug: "blobert", name: "Blobert" },
  ] as never);
  useUiStore.setState({ workspace: "all", queueWorkspaceSlug: "blobert" });

  const queue = wrap(<div>queue body</div>, "/queue");
  await waitFor(() => expect(mockSidebarProps.at(-1)?.workspaceSlug).toBe("loregarden"));
  queue.unmount();

  wrap(<div>console body</div>, "/console");
  await waitFor(() => expect(mockSidebarProps.at(-1)?.workspaceSlug).toBe("loregarden"));

  useUiStore.setState({ workspace: "blobert" });
  await waitFor(() => expect(mockSidebarProps.at(-1)?.workspaceSlug).toBe("blobert"));
});

it("applies right dock body class when edge is right", () => {
  useUiStore.setState({ utilityDockEdge: "right" });
  const { container } = wrap(<div>home</div>, "/");
  expect(container.querySelector(".app-body--dock-right")).toBeTruthy();
  expect(container.querySelector(".app-utility-dock--right")).toBeTruthy();
});
