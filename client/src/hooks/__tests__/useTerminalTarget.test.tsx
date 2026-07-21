import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import { useTerminalTarget } from "../useTerminalTarget";
import { useUiStore } from "../../state/uiStore";

jest.mock("../../api/client", () => ({
  api: { ticket: jest.fn() },
}));

const mockTicket = api.ticket as jest.MockedFunction<typeof api.ticket>;

function wrapperFor(path: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return (
      <QueryClientProvider client={client}>
        {/* Mirrors the real tree: the dock mounts above <Routes>. */}
        <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({
    workspace: "all",
    branchTriageWorkspaceSlug: "",
    branchTriageBranch: "",
  });
});

it("opens the shell in the ticket's workspace, not the filter's", () => {
  // The filter can say "all" while a ticket is on screen. The ticket wins:
  // it names an actual directory.
  useUiStore.setState({ workspace: "all" });
  mockTicket.mockResolvedValue({
    workspace_slug: "blobert",
    branch: "feat/x",
    next_agent: "implementer",
  } as never);

  const { result } = renderHook(() => useTerminalTarget(), {
    wrapper: wrapperFor("/tickets/t-42"),
  });

  return waitFor(() => {
    expect(result.current).toEqual({ workspaceSlug: "blobert", agent: "implementer" });
  });
});

it("follows branch triage to that workspace", async () => {
  useUiStore.setState({
    branchTriageWorkspaceSlug: "loregarden",
    branchTriageBranch: "u3d-terminal-dock",
  });

  const { result } = renderHook(() => useTerminalTarget(), {
    wrapper: wrapperFor("/branch-triage"),
  });

  await waitFor(() => expect(result.current.workspaceSlug).toBe("loregarden"));
  // No branch is returned at all: the shell opens in the workspace root, so a
  // branch label would be true only by coincidence.
  expect(result.current).not.toHaveProperty("branch");
  expect(result.current.agent).toBe("");
  expect(mockTicket).not.toHaveBeenCalled();
});

it("falls back to the workspace filter when no ticket is on screen", () => {
  useUiStore.setState({ workspace: "blobert" });

  const { result } = renderHook(() => useTerminalTarget(), { wrapper: wrapperFor("/") });

  expect(result.current.workspaceSlug).toBe("blobert");
});

it("names no workspace when the filter is showing all of them", () => {
  // "all" is a filter value, not a slug — there is no directory to cd into,
  // and starting a shell in the wrong repo is worse than not starting one.
  useUiStore.setState({ workspace: "all" });

  const { result } = renderHook(() => useTerminalTarget(), { wrapper: wrapperFor("/") });

  expect(result.current.workspaceSlug).toBe("");
});

it("asks for no ticket when the route has none", () => {
  renderHook(() => useTerminalTarget(), { wrapper: wrapperFor("/queue") });

  expect(mockTicket).not.toHaveBeenCalled();
});
